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


def _m(obj, *names):
    """First matching attribute (snake_case 0.7.1 vs camelCase bindings)."""
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    raise AttributeError(f"none of {names} on {type(obj).__name__}")


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


def capture_dual(rgb_id, ir_id, rgb_size=(640, 480), nbuf=16, timeout=25,
                 on_streaming=None):
    """Returns {'rgb': bgr|None, 'ir': bgr|None}. Never returns Nones pair
    without raising."""
    import libcamera
    from libcamera import CameraManager, FrameBufferAllocator, StreamRole

    cm = CameraManager.singleton()
    cams = {}
    _cams = getattr(cm, "cameras", None)
    _cam_list = _cams() if callable(_cams) else _cams

    def open_one(match, role, size=None, want_bufs=8):
        cam = next(c for c in _cam_list if match in c.id)
        _m(cam, "acquire")()
        try:
            cfg = _m(cam, "generate_configuration",
                      "generateConfiguration")([role])
            sc = cfg.at(0)
            if size is not None:
                try:
                    sc.size = libcamera.Size(size[0], size[1])
                except Exception:
                    pass
            try:
                sc.buffer_count = want_bufs
            except Exception:
                try:
                    sc.bufferCount = want_bufs
                except Exception:
                    pass
            ret = _m(cam, "configure")(cfg)
            if ret is not None and ret < 0:
                raise RuntimeError(f"{match}: configure failed")
            sc = cfg.at(0)  # re-read (driver may adjust)
            alloc = FrameBufferAllocator(cam)
            n = _m(alloc, "allocate")(sc.stream)
            if n is not None and n <= 0:
                raise RuntimeError(f"{match}: allocate failed")
            sz = getattr(sc, "size", None) or getattr(sc, "size_", None)
            w, h = (sz.width, sz.height) if sz is not None else (0, 0)
            return cam, sc, alloc, (w, h)
        except Exception:
            try:
                _m(cam, "release")()
            except Exception:
                pass
            raise

    out = {"rgb": None, "ir": None}
    rgb = ir = None
    try:
        rgb, rgb_sc, rgb_alloc, rgb_wh = open_one(
            rgb_id, StreamRole.Viewfinder, rgb_size, nbuf)
        ir, ir_sc, ir_alloc, ir_wh = open_one(ir_id, StreamRole.Raw,
                                             None, nbuf)
        jobs = [("rgb", rgb, rgb_sc, rgb_alloc),
                ("ir", ir, ir_sc, ir_alloc)]
        cookie = {}
        targets = {}
        nxt = [1]

        def queue_all():
            for idx, (name, cam, sc, alloc) in enumerate(jobs):
                bufs = _m(alloc, "buffers")(sc.stream)[:nbuf]
                targets[name] = len(bufs)
                for b in bufs:
                    req = _m(cam, "create_request",
                             "createRequest")(nxt[0])
                    cookie[nxt[0]] = (name, b)
                    nxt[0] += 1
                    _m(req, "add_buffer", "addBuffer")(sc.stream, b)
                    _m(cam, "queue_request", "queueRequest")(req)

        for _, cam, _, _ in jobs:
            _m(cam, "start")()
        queue_all()
        if on_streaming is not None:
            on_streaming()
        got = {}
        counts = {"rgb": 0, "ir": 0}
        evfd = _m(cm, "event_fd", "eventFd")
        ready = _m(cm, "get_ready_requests", "getReadyRequests")
        deadline = time.time() + timeout
        while time.time() < deadline and \
                (counts["rgb"] < targets.get("rgb", nbuf) or
                 counts["ir"] < targets.get("ir", nbuf)):
            select.select([evfd() if callable(evfd) else evfd], [], [], 1.0)
            for req in ready():
                st = _m(req, "status")
                complete = getattr(getattr(req, "Status", req),
                                   "Complete", None)
                if complete is not None and st != complete:
                    continue
                name, fb = cookie.get(_m(req, "cookie"), (None, None))
                if name is None or fb is None:
                    continue
                counts[name] = counts.get(name, 0) + 1
                got[name] = fb  # keep last completed buffer per camera
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
                for op in ("stop", "release"):
                    try:
                        _m(cam, op)()
                    except Exception:
                        pass
    if out["rgb"] is None and out["ir"] is None:
        raise RuntimeError("dual capture: no completed buffers")
    return out
