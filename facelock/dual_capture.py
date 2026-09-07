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


def _meta_exp_gain(req):
    """Extract (exposure_us, analogue_gain) from request metadata.

    pycamera exposes metadata as a dict keyed by ControlId with .name.
    Returns (None, None) when a key is missing — never raises.
    """
    exp = gain = None
    try:
        md = _m(req, "metadata")
        items = md.items() if hasattr(md, "items") else []
        for key, val in items:
            nm = getattr(key, "name", "") or ""
            if nm == "ExposureTime" and exp is None:
                try:
                    exp = int(val)
                except (TypeError, ValueError):
                    pass
            elif nm == "AnalogueGain" and gain is None:
                try:
                    gain = float(val)
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass
    return exp, gain


def _decode_abgr(blob, w, h, stride):
    # Despite the ABGR8888 label, SoftISP lays out bytes B,G,R,A in memory
    # (verified: 4th byte saturates = alpha). BGR is the first 3 bytes.
    a = np.frombuffer(blob, dtype=np.uint8).reshape(h, stride)
    px = a[:, :w * 4].reshape(h, w, 4)
    return px[:, :, 0:3].copy()


def capture_dual(rgb_id, ir_id, rgb_size=(640, 480), nbuf=16, timeout=25,
                 on_streaming=None, settle=None, stats_cb=None):
    """Returns {'rgb': bgr|None, 'ir': bgr|None}. Never returns Nones pair
    without raising.

    settle: dict|None. When given, frames stream until per-source
      exposure/gain stop moving (AEGC settled) instead of a fixed count:
      {'stable': 4,          # consecutive frames within tolerance
       'exp_tol': 0.10,      # relative exposure change
       'gain_tol': 0.05,     # absolute gain change
       'min_frames': 4,      # always take at least these
       'max_frames': 60}     # hard cap per source
    stats_cb: optional callable(name, index, exp, gain) per completed frame.
    Returns after settle or timeout; settle state is reported via the
    'stats' key in the returned... (see capture_dual_stats).
    """
    return capture_dual_stats(
        rgb_id, ir_id, rgb_size, nbuf, timeout, on_streaming, settle,
        stats_cb)[0]


def capture_dual_stats(rgb_id, ir_id, rgb_size=(640, 480), nbuf=16,
                       timeout=25, on_streaming=None, settle=None,
                       stats_cb=None, frame_cb=None):
    """capture_dual + per-source stats dict.

    stats[name] = {'frames': int, 'settled': bool,
                   'exp': last exposure, 'gain': last gain,
                   'exp_hist': [...], 'gain_hist': [...]}
    frame_cb(name, frame_index, bgr_image): called per kept frame, before
    the last-frame decode — lets the M2 liveness burst collect IR history.
    """
    import libcamera
    from libcamera import CameraManager, FrameBufferAllocator, StreamRole

    settle = settle or {}
    want_stable = int(settle.get("stable", 0) or 0)
    exp_tol = float(settle.get("exp_tol", 0.10))
    gain_tol = float(settle.get("gain_tol", 0.05))
    min_frames = int(settle.get("min_frames", 0) or 0)
    max_frames = int(settle.get("max_frames", 60))
    use_settle = want_stable > 0

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
        stats = {n: {"frames": 0, "settled": False, "exp": None,
                     "gain": None, "exp_hist": [], "gain_hist": [],
                     "stable_run": 0} for n in ("rgb", "ir")}
        stable_need = {n: targets.get(n, nbuf) for n in ("rgb", "ir")}
        if use_settle:
            # per-source settle replaces the fixed completion count
            stable_need = {"rgb": want_stable, "ir": want_stable}
        evfd = _m(cm, "event_fd", "eventFd")
        ready = _m(cm, "get_ready_requests", "getReadyRequests")
        deadline = time.time() + timeout

        def src_done(n):
            s = stats[n]
            if use_settle:
                return s["settled"] or s["frames"] >= max_frames
            return counts[n] >= targets.get(n, nbuf)

        while time.time() < deadline and \
                not (src_done("rgb") and src_done("ir")):
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
                exp, gain = _meta_exp_gain(req)
                s = stats[name]
                s["frames"] += 1
                prev_e, prev_g = s["exp"], s["gain"]
                if exp is not None:
                    s["exp"] = exp
                    s["exp_hist"].append(exp)
                if gain is not None:
                    s["gain"] = gain
                    s["gain_hist"].append(gain)
                if stats_cb is not None:
                    try:
                        stats_cb(name, s["frames"], exp, gain)
                    except Exception:
                        pass
                if use_settle and s["frames"] >= min_frames and \
                        prev_e is not None and exp is not None and \
                        prev_g is not None and gain is not None:
                    de = abs(exp - prev_e) / max(abs(prev_e), 1)
                    dg = abs(gain - prev_g)
                    if de <= exp_tol and dg <= gain_tol:
                        s["stable_run"] += 1
                    else:
                        s["stable_run"] = 0
                    if s["stable_run"] >= want_stable:
                        s["settled"] = True
                counts[name] = counts.get(name, 0) + 1
                got[name] = fb  # keep last completed buffer per camera
                if frame_cb is not None:
                    try:
                        frame_cb(name, counts[name] - 1, fb)
                    except Exception:
                        pass
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
    for s in stats.values():
        s.pop("stable_run", None)
    return out, stats
