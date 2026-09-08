"""One CameraManager, reusable requests and copied, timestamped frames.

An unavailable route, cancelled request or decode error aborts capture.
"""
from dataclasses import dataclass
import mmap
import select
import time

import cv2
import numpy as np


def _m(obj, *names):
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    raise AttributeError(f"missing {names} on {type(obj).__name__}")


def _value(obj, *names):
    value = _m(obj, *names)
    return value() if callable(value) else value


def _planes(fb):
    return _value(fb, "planes")


def _read_plane(plane):
    offset = int(plane.offset)
    base = offset - offset % mmap.PAGESIZE
    delta = offset - base
    with mmap.mmap(plane.fd, int(plane.length) + delta,
                   access=mmap.ACCESS_READ, offset=base) as mapped:
        return mapped[delta:delta + int(plane.length)]


def decode_rgb(blob, w, h, stride, pixel_format):
    """FOURCC word order with little-endian byte storage, converted to BGR."""
    channels = {
        "ABGR8888": (4, (2, 1, 0)), "XBGR8888": (4, (2, 1, 0)),
        "ARGB8888": (4, (0, 1, 2)), "XRGB8888": (4, (0, 1, 2)),
        "RGB888": (3, (0, 1, 2)), "BGR888": (3, (2, 1, 0)),
    }
    if pixel_format not in channels:
        raise ValueError(f"unsupported RGB format: {pixel_format}")
    bpp, order = channels[pixel_format]
    if stride < w * bpp or len(blob) < h * stride:
        raise ValueError("short RGB plane")
    rows = np.frombuffer(blob, np.uint8, count=h * stride).reshape(h, stride)
    pixels = rows[:, :w * bpp].reshape(h, w, bpp)
    return np.ascontiguousarray(pixels[:, :, order])


@dataclass
class Frame:
    source: str
    image: np.ndarray
    timestamp_ns: int
    sequence: int
    exposure: float | None
    gain: float | None
    colour_gains: tuple | None = None


class CameraSession:
    def __init__(self, cameras, rgb_size=(640, 480), buffers=4):
        if not 2 <= buffers <= 16:
            raise ValueError("camera buffers must be in 2..16")
        self.cameras, self.rgb_size, self.buffers = cameras, rgb_size, buffers
        self.jobs, self.requests, self.started = {}, {}, []
        self.cm = None

    def __enter__(self):
        import libcamera
        self.libcamera = libcamera
        self.cm = libcamera.CameraManager.singleton()
        available = list(_value(self.cm, "cameras"))
        try:
            for name, camera_id in self.cameras.items():
                matches = [c for c in available if camera_id and camera_id in c.id]
                if len(matches) != 1:
                    raise RuntimeError(f"{name}: expected one camera for {camera_id!r}")
                cam = matches[0]
                cam.acquire()
                self.jobs[name] = {"cam": cam}
                role = (libcamera.StreamRole.Raw if name == "ir"
                        else libcamera.StreamRole.Viewfinder)
                cfg = _m(cam, "generate_configuration", "generateConfiguration")([role])
                sc = cfg.at(0)
                if name == "rgb":
                    sc.size = libcamera.Size(*self.rgb_size)
                if hasattr(sc, "buffer_count"):
                    sc.buffer_count = self.buffers
                else:
                    sc.bufferCount = self.buffers
                cfg.validate()
                cam.configure(cfg)
                sc = cfg.at(0)
                alloc = libcamera.FrameBufferAllocator(cam)
                alloc.allocate(sc.stream)
                self.jobs[name].update(sc=sc, cfg=cfg, alloc=alloc,
                                       format=str(_value(sc, "pixel_format", "pixelFormat")))
                allocated = alloc.buffers(sc.stream)[:self.buffers]
                if len(allocated) < 2:
                    raise RuntimeError(f"{name}: insufficient camera buffers")
                for fb in allocated:
                    cookie = len(self.requests) + 1
                    req = _m(cam, "create_request", "createRequest")(cookie)
                    _m(req, "add_buffer", "addBuffer")(sc.stream, fb)
                    self.requests[cookie] = (name, fb, req)
            for job in self.jobs.values():
                job["cam"].start()
                self.started.append(job["cam"])
            for name, _, req in self.requests.values():
                _m(self.jobs[name]["cam"], "queue_request", "queueRequest")(req)
            self.event_fd = _value(self.cm, "event_fd", "eventFd")
            return self
        except BaseException:
            self.close()
            raise

    def read(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("camera capture deadline exceeded")
        if not select.select([self.event_fd], [], [], min(remaining, 0.5))[0]:
            return []
        frames = []
        for req in _m(self.cm, "get_ready_requests", "getReadyRequests")():
            name, fb, _ = self.requests[_value(req, "cookie")]
            if _value(req, "status") != req.Status.Complete:
                raise RuntimeError(f"{name}: request did not complete")
            md = _value(fb, "metadata")
            if md.status != self.libcamera.FrameMetadata.Status.Success:
                raise RuntimeError(f"{name}: frame buffer error")
            job = self.jobs[name]
            sc = job["sc"]
            w, h, stride = sc.size.width, sc.size.height, int(sc.stride)
            blob = _read_plane(_planes(fb)[0])
            used = int(_value(md.planes[0], "bytes_used", "bytesused"))
            if used < h * stride:
                raise ValueError(f"{name}: incomplete frame payload")
            blob = blob[:used]
            if name == "ir":
                if job["format"] != "R10_CSI2P":
                    raise ValueError(f"unsupported IR format: {job['format']}")
                from .ir_capture import unpack_r10_bytes
                gray = unpack_r10_bytes(blob[:h * stride], w, h, stride)
                img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
            else:
                img = decode_rgb(blob, w, h, stride, job["format"])
            controls = {getattr(k, "name", str(k)): v
                        for k, v in _value(req, "metadata").items()}
            frames.append(Frame(name, img, int(md.timestamp), int(md.sequence),
                                controls.get("ExposureTime"), controls.get("AnalogueGain"),
                                tuple(controls["ColourGains"]) if "ColourGains" in controls else None))
            # Copy before reuse; the device will overwrite this same dma-buf.
            req.reuse()
            _m(job["cam"], "queue_request", "queueRequest")(req)
        return frames

    def close(self):
        for cam in reversed(self.started):
            try:
                cam.stop()
            except RuntimeError:
                pass
        self.started.clear()
        if self.cm is not None:
            try:
                _m(self.cm, "get_ready_requests", "getReadyRequests")()
            except RuntimeError:
                pass
        self.requests.clear()
        for job in reversed(list(self.jobs.values())):
            try:
                job["cam"].release()
            except RuntimeError:
                pass
        self.jobs.clear()

    def __exit__(self, *exc):
        self.close()


class UvcSession:
    """Explicit RGB-only backend for a USB webcam."""
    def __init__(self, cameras, rgb_size=(640, 480), buffers=4):
        self.device = cameras["rgb"].removeprefix("uvc:")
        self.size = rgb_size
        self.cap = None
        self.sequence = 0

    def __enter__(self):
        self.cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            self.cap.release()
            raise RuntimeError(f"cannot open {self.device}")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.size[0])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.size[1])
        return self

    def read(self, deadline):
        if time.monotonic() >= deadline:
            raise TimeoutError("UVC capture deadline exceeded")
        ok, img = self.cap.read()
        if not ok or img is None:
            raise RuntimeError("UVC capture failed")
        self.sequence += 1
        return [Frame("rgb", img.copy(), time.monotonic_ns(), self.sequence, None, None)]

    def __exit__(self, *exc):
        self.cap.release()


def capture_dual_stats(rgb_id, ir_id, rgb_size=(640, 480), nbuf=4,
                       timeout=15, on_streaming=None, settle=None,
                       stats_cb=None, frame_cb=None):
    """Diagnostic compatibility API; authentication uses acquisition.capture()."""
    target = max(16, int((settle or {}).get("min_frames", 16)))
    out, stats = {}, {}
    deadline = time.monotonic() + timeout
    with CameraSession({"rgb": rgb_id, "ir": ir_id}, rgb_size, min(nbuf, 16)) as session:
        if on_streaming:
            on_streaming()
        while any(stats.get(s, {}).get("frames", 0) < target for s in ("rgb", "ir")):
            for frame in session.read(deadline):
                name = frame.source
                count = stats.get(name, {}).get("frames", 0) + 1
                out[name] = frame.image
                stats[name] = {"frames": count, "exp": frame.exposure,
                               "gain": frame.gain, "settled": False}
                if stats_cb:
                    stats_cb(name, count, frame.exposure, frame.gain)
                if frame_cb:
                    frame_cb(name, count - 1, frame.image)
    return out, stats


def capture_dual(*args, **kwargs):
    return capture_dual_stats(*args, **kwargs)[0]
