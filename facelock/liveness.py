"""Active illumination checks, not a validated presentation-attack detector.

Paper and displays can reflect IR and acquire sensor noise too. Correlation
establishes a response to this attempt's light, not that a person is live.
"""
import hashlib
import math
import os
from pathlib import Path
import time

import numpy as np


def make_nonce():
    return os.urandom(32).hex()


def challenge_pattern(nonce, n_frames=8):
    """Balanced nonce-derived phases, with at least three of each state."""
    if type(n_frames) is not int or not 6 <= n_frames <= 32 or n_frames % 2:
        raise ValueError("challenge phases must be even and between 6 and 32")
    order = sorted(range(n_frames), key=lambda i: hashlib.sha256(
        f"{nonce}:{i}".encode()).digest())
    lit = set(order[:n_frames // 2])
    return [int(i in lit) for i in range(n_frames)]


class TorchDriver:
    """Checked torch writes; never fires the latched flash channel."""
    def __init__(self, brightness_path, brightness, strobe_path=None):
        self.path = Path(brightness_path)
        self.strobe = Path(strobe_path) if strobe_path else None
        self.brightness = int(brightness)

    def __enter__(self):
        try:
            maximum = int((self.path.parent / "max_brightness").read_text())
            if not 1 <= self.brightness <= maximum:
                raise ValueError(f"torch brightness must be in 1..{maximum}")
            self.off()
            return self
        except BaseException:
            self.off()
            raise

    def set(self, enabled):
        self.path.write_text(str(self.brightness if enabled else 0))

    def off(self):
        error = None
        for path in (self.strobe, self.path):
            if path is not None:
                try:
                    path.write_text("0")
                except OSError as exc:
                    error = exc
        if error:
            raise error

    def __exit__(self, *exc):
        self.off()


class FlashDriver(TorchDriver):
    """Use the existing flash current/timeout; re-arm each short lit phase."""
    def __enter__(self):
        if self.strobe is None:
            raise ValueError("flash mode needs ir_led.strobe_path")
        try:
            self.off()
            self.timeout = int((self.strobe.parent / "flash_timeout").read_text()) / 1e6
            if not 0.2 <= self.timeout <= 2:
                raise ValueError("flash timeout must be between 0.2 and 2 seconds")
            self.expires = None
            return self
        except BaseException:
            self.off()
            raise

    def set(self, enabled):
        self.off()
        self.expires = None
        if enabled:
            time.sleep(0.05)
            self.strobe.write_text("1")
            self.expires = time.monotonic() + self.timeout

    def check(self):
        if self.expires is not None and time.monotonic() >= self.expires:
            raise RuntimeError("flash expired before illumination phase completed")


def illumination_driver(led):
    cls = {"torch": TorchDriver, "flash": FlashDriver}[led["mode"]]
    return cls(led["path"], led["brightness"], led.get("strobe_path"))


def check_challenge(gray_frames, pattern, min_gap=8.0, roi=None):
    if len(gray_frames) != len(pattern) or not gray_frames:
        return False, {"reason": "length-mismatch"}
    if not math.isfinite(min_gap) or min_gap <= 0:
        raise ValueError("min_gap must be positive and finite")
    if any(p not in (0, 1) for p in pattern):
        return False, {"reason": "invalid-pattern"}
    means = []
    for frame in gray_frames:
        a = np.asarray(frame)
        if a.ndim != 2:
            return False, {"reason": "invalid-frame"}
        if roi is not None:
            x, y, w, h = roi
            a = a[y:y + h, x:x + w]
        if not a.size or not np.isfinite(a).all():
            return False, {"reason": "invalid-frame"}
        means.append(float(a.mean()))
    lit = [m for m, p in zip(means, pattern) if p]
    dark = [m for m, p in zip(means, pattern) if not p]
    detail = {"means": [round(m, 2) for m in means],
              "pattern": [int(p) for p in pattern]}
    if min(len(lit), len(dark)) < 3:
        return False, {**detail, "reason": "pattern-degenerate"}
    gap = min(lit) - max(dark)
    ok = gap >= min_gap
    return ok, {**detail, "gap": round(gap, 2),
                "reason": "ok" if ok else "no-illumination-correlation"}


def temporal_noise(frames_gray):
    """Measure equally illuminated frames; do not issue a liveness verdict."""
    if len(frames_gray) < 3:
        return {"reason": "too-few-frames"}
    a = np.stack(frames_gray).astype(np.float32)
    if not np.isfinite(a).all():
        return {"reason": "invalid-frame"}
    return {"std_median": round(float(np.median(a.std(axis=0))), 2),
            "frames": len(frames_gray), "use": "diagnostic-only"}


def fuse(results, required_sensors):
    required = tuple(required_sensors)
    if not required or len(required) != len(set(required)) or set(required) - {"rgb", "ir"}:
        raise ValueError("invalid required sensors")
    accepted = all(results.get(s, {}).get("match") is True for s in required)
    return accepted, {"required": list(required), "accepted": accepted}
