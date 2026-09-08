"""Shared enrollment/verification acquisition with explicit illumination phases."""
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass, field
import time

import cv2

from .dual_capture import CameraSession, UvcSession
from .liveness import illumination_driver, challenge_pattern, make_nonce


@dataclass
class Burst:
    images: dict
    ir_frames: list
    pattern: list
    stats: dict
    depth_frames: list = field(default_factory=list)
    stereo_pairs: list = field(default_factory=list)


def fit_frame(image, width, height):
    """Downsample the complete field of view; never stretch, crop, or upscale."""
    h, w = image.shape[:2]
    scale = min(width / w, height / h, 1.0)
    if scale == 1.0:
        return image
    return cv2.resize(image, (max(1, round(w * scale)), max(1, round(h * scale))),
                      interpolation=cv2.INTER_AREA)


class PhaseCollector:
    """Discard queued/transition exposures before assigning an illumination label."""
    def __init__(self, pattern, driver, guard_ms=100, discard_frames=2,
                 samples_per_phase=2, clock=time.monotonic_ns):
        self.pattern, self.driver = pattern, driver
        self.guard_ns = int(guard_ms * 1_000_000)
        self.discard_frames, self.samples_per_phase = discard_frames, samples_per_phase
        self.clock = clock
        self.index, self.seen, self.kept = 0, 0, 0
        self.frames, self.flags, self.lit = [], [], []
        self.last_lit = None
        self.last_timestamp = 0
        self.last_sequence = -1
        self._switch()

    @property
    def done(self):
        return self.index == len(self.pattern)

    def _switch(self):
        self.driver.set(self.pattern[self.index])
        self.after_ns = self.clock() + self.guard_ns
        self.seen = self.kept = 0

    def add(self, frame):
        if self.done:
            return
        if frame.timestamp_ns <= self.last_timestamp or frame.sequence <= self.last_sequence:
            raise RuntimeError("IR timestamps or sequences missing or out of order")
        self.last_timestamp = frame.timestamp_ns
        self.last_sequence = frame.sequence
        self.seen += 1
        if self.seen <= self.discard_frames or frame.timestamp_ns < self.after_ns:
            return
        lit = self.pattern[self.index]
        self.frames.append(cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY))
        self.flags.append(lit)
        if lit:
            self.lit.append(frame.image)
            self.last_lit = frame
        self.kept += 1
        if self.kept == self.samples_per_phase:
            self.index += 1
            if not self.done:
                self._switch()


def capture(cfg, session_factory=CameraSession, driver_factory=illumination_driver,
            collect_stereo=False):
    if cfg["runtime"]["require_camss"]:
        from .kernel import require_camss
        require_camss()
    required = cfg["auth"]["required_sensors"]
    mode = cfg["capture"]["mode"]
    pairs = reconstruction = None
    if cfg["depth"]["enabled"] or collect_stereo:
        from .stereo import PairCollector, Reconstruction
        if mode != "dual-pycamera" or set(required) != {"rgb", "ir"}:
            raise ValueError("stereo needs concurrent rgb+ir capture")
        pairs = PairCollector(cfg["depth"]["max_skew_ms"])
        if cfg["depth"]["enabled"]:
            reconstruction = Reconstruction(cfg)
    if mode == "sequential" and len(required) > 1:
        # Explicit choice, never entered as an error fallback.
        deadline = time.monotonic() + cfg["capture"]["timeout_sec"]
        combined = Burst({}, [], [], {})
        for source in required:
            one = deepcopy(cfg)
            one["auth"]["required_sensors"] = [source]
            one["capture"]["timeout_sec"] = deadline - time.monotonic()
            burst = capture(one, session_factory, driver_factory)
            combined.images.update(burst.images)
            combined.stats[source] = burst.stats[source]
            if source == "ir":
                combined.ir_frames, combined.pattern = burst.ir_frames, burst.pattern
        combined.stats["capture_s"] = round(cfg["capture"]["timeout_sec"] - (deadline - time.monotonic()), 3)
        return combined
    if mode == "uvc" and session_factory is CameraSession:
        session_factory = UvcSession
    d = cfg["dual"]
    led = cfg["ir_led"]
    challenge = cfg["challenge"]
    start = time.monotonic()
    deadline = start + cfg["capture"]["timeout_sec"]
    manager = (driver_factory(led)
               if "ir" in required else nullcontext())
    stats = {s: {"frames": 0, "exp": None, "gain": None,
                 "settled": False, "stable_run": 0} for s in required}
    images = {s: [] for s in required}
    collector = None
    with manager as driver:
        with session_factory({s: cfg["cameras"][s] for s in required},
                             (d["rgb_width"], d["rgb_height"]), d["buffers"]) as session:
            warmup_start = time.monotonic()
            # RGB AEGC needs recycled requests. IR stays dark during warmup.
            warmup = True
            while warmup or (collector is not None and not collector.done):
                if driver is not None and hasattr(driver, "check"):
                    driver.check()
                for frame in session.read(deadline):
                    name, st = frame.source, stats[frame.source]
                    previous_e, previous_g = st["exp"], st["gain"]
                    st["frames"] += 1
                    st["exp"], st["gain"] = frame.exposure, frame.gain
                    if frame.colour_gains is not None:
                        st["colour_gains"] = list(frame.colour_gains)
                    if all(v is not None for v in (previous_e, previous_g,
                                                    frame.exposure, frame.gain)):
                        stable = (abs(frame.exposure - previous_e) / max(abs(previous_e), 1)
                                  <= d["settle"]["exp_tol"] and
                                  abs(frame.gain - previous_g) <= d["settle"]["gain_tol"])
                        st["stable_run"] = st["stable_run"] + 1 if stable else 0
                        st["settled"] = st["stable_run"] >= d["settle"]["stable"]
                    if name == "rgb":
                        # SoftISP's requested stream size is a centre crop, not
                        # a scaler. Capture the native view, resize only here.
                        image = fit_frame(frame.image, cfg["capture"]["width"],
                                          cfg["capture"]["height"])
                        if pairs is not None:
                            pairs.add_rgb(frame, image)
                        images[name] = (images[name] + [image])[-3:]
                        st["sensor_size"] = list(frame.image.shape[1::-1])
                        st["image_size"] = list(image.shape[1::-1])
                    if not warmup and collector is not None and name == "ir":
                        collector.add(frame)
                        if pairs is not None and collector.last_lit is frame:
                            pairs.add_ir(frame)
                if warmup:
                    elapsed = time.monotonic() - warmup_start
                    enough = all(s["frames"] >= d["settle"]["min_frames"] for s in stats.values())
                    rgb = stats.get("rgb")
                    # Missing metadata gets bounded timed warmup, never a false settled flag.
                    stable = rgb is None or rgb["settled"] or rgb["exp"] is None or rgb["gain"] is None
                    cap = any(s["frames"] >= d["settle"]["max_frames"] for s in stats.values())
                    if enough and elapsed >= cfg["capture"]["warmup_sec"] and (stable or cap):
                        warmup = False
                        if driver is not None:
                            collector = PhaseCollector(
                                challenge_pattern(make_nonce(), challenge["phases"]), driver,
                                challenge["guard_ms"], challenge["discard_frames"],
                                challenge["samples_per_phase"])
    if collector is not None:
        images["ir"] = collector.lit[-3:]
    for st in stats.values():
        st.pop("stable_run")
    stats["capture_s"] = round(time.monotonic() - start, 3)
    result = Burst(images, collector.frames if collector else [],
                   collector.flags if collector else [], stats)
    if pairs is not None:
        result.stereo_pairs = pairs.finish()
        stats["stereo"] = {"pairs": len(result.stereo_pairs),
                           "skew_ms": [round(abs(p[2] - p[3]) / 1e6, 3)
                                       for p in result.stereo_pairs]}
        if reconstruction is not None:
            result.images["rgb"] = [p[0] for p in result.stereo_pairs]
            result.images["ir"] = [p[1] for p in result.stereo_pairs]
            result.depth_frames = [reconstruction.compute(p[0], p[1]) for p in result.stereo_pairs]
    return result
