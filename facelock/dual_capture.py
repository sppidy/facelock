"""Single-process concurrent RGB+IR capture (staged pycamera, 0.7.1 API).

Runs both sensors in ONE CameraManager so the disjoint-routes allocator
can place them on separate CSID/VFE paths. Must execute under the staged
stack (see facelock-run wrapper). Raises on catastrophic failure so callers
can fall back to the legacy sequential paths.
"""
import mmap
import os
import select
import sys
import time

import cv2
import numpy as np


def _planes(fb):
    p = getattr(fb, "planes", None)
    return p() if callable(p) else p


def _read_plane(plane):
    m = mmap.mmap(plane.fd, plane.length,
                  access=mmap.ACCESS_READ, offset=plane.offset)
    try:
        return bytes(m)
    finally:
        m.close()


def _decode_abgr(blob, w, h, stride):
    # Despite the ABGR8888 label, SoftISP lays out bytes B,G,R,A in memory
    # (verified: 4th byte saturates = alpha). BGR is the first 3 bytes.
    a = np.frombuffer(blob, dtype=np.uint8).reshape(h, stride)
    px = a[:, :w * 4].reshape(h, w, 4)
    return px[:, :, 0:3].copy()


def capture_dual(rgb_id, ir_id, rgb_size=(640, 480), nbuf=16, timeout=25):
    """Returns {'rgb': bgr|None, 'ir': bgr|None}. Never returns Nones pair
    without raising."""
    import libcamera
    from libcamera import CameraManager, FrameBufferAllocator, StreamRole

    cm = CameraManager.singleton()
    cams = {}

    def open_one(match, role, size=None):
        cam = next(c for c in cm.cameras if match in c.id)
        cam.acquire()
        try:
            cfg = cam.generate_configuration([role])
            sc = cfg.at(0)
            if size is not None:
                try:
                    sc.size = libcamera.Size(size[0], size[1])
                except Exception:
                    pass
            ret = cam.configure(cfg)
            if ret is not None and ret < 0:
                raise RuntimeError(f"{match}: configure failed")
            sc = cfg.at(0)  # re-read (driver may adjust)
            alloc = FrameBufferAllocator(cam)
            n = alloc.allocate(sc.stream)
            if n is not None and n <= 0:
                raise RuntimeError(f"{match}: allocate failed")
            w, h = sc.size.width, sc.size.height
            return cam, sc, alloc, (w, h)
        except Exception:
            cam.release()
            raise

    out = {"rgb": None, "ir": None}
    rgb = ir = None
    try:
        rgb, rgb_sc, rgb_alloc, rgb_wh = open_one(
            rgb_id, StreamRole.Viewfinder, rgb_size)
        ir, ir_sc, ir_alloc, ir_wh = open_one(ir_id, StreamRole.Raw)
        jobs = [("rgb", rgb, rgb_sc, rgb_alloc),
                ("ir", ir, ir_sc, ir_alloc)]
        cookie = {}
        nxt = [1]

        def queue_all():
            for idx, (name, cam, sc, alloc) in enumerate(jobs):
                bufs = alloc.buffers(sc.stream)[:nbuf]
                for b in bufs:
                    req = cam.create_request(nxt[0])
                    cookie[nxt[0]] = (name, b)
                    nxt[0] += 1
                    req.add_buffer(sc.stream, b)
                    cam.queue_request(req)

        for _, cam, _, _ in jobs:
            cam.start()
        queue_all()
        got = {}
        deadline = time.time() + timeout
        while time.time() < deadline and len(got) < 2 * nbuf:
            select.select([cm.event_fd], [], [], 1.0)
            for req in cm.get_ready_requests():
                if req.status != req.Status.Complete:
                    continue
                name, fb = cookie.get(req.cookie, (None, None))
                if name is None or name in got:
                    # keep last completed buffer per camera
                    pass
                if fb is not None:
                    got[name] = fb
        # decode last completed buffer per camera
        if "rgb" in got:
            w, h = rgb_wh
            planes = _planes(got["rgb"])
            blob = _read_plane(planes[0])
            stride = len(blob) // h
            out["rgb"] = _decode_abgr(blob, w, h, stride)
        if "ir" in got:
            from facelock.ir_capture import unpack_r10_bytes
            w, h = ir_wh
            planes = _planes(got["ir"])
            blob = _read_plane(planes[0])
            stride = len(blob) // h
            gray = unpack_r10_bytes(blob, w, h, stride)
            out["ir"] = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    finally:
        for cam in (rgb, ir):
            if cam is not None:
                try:
                    cam.stop()
                except Exception:
                    pass
                try:
                    cam.release()
                except Exception:
                    pass
    if out["rgb"] is None and out["ir"] is None:
        raise RuntimeError("dual capture: no completed buffers")
    return out
