"""M2 liveness: strobe challenge-response, IR texture, fusion scoring.

Attack model (both classes, per plan):
- photo/print: flat texture, no per-pixel temporal noise, follows strobe
  only via ambient reflection (weak), no correlation structure
- screencast replay: RGB emissive (bright screen), IR dark (screens emit
  almost no NIR), texture shows LCD pixel-grid + compression noise

Challenge: per-attempt random strobe pattern; attacker cannot know which
frames will be lit, so a static print/screen cannot selectively brighten.
We verify (a) lit frames are lit, (b) dark frames are dark, in IR.
"""
import hashlib
import os
import time

try:
    import numpy as np
except ImportError:  # stdlib-only unit tests
    np = None

from .ir_capture import set_led

STROBE_WINDOW_S = 1.28  # PMIC flash_timeout on this board


def make_nonce():
    """Per-attempt challenge seed."""
    return os.urandom(16).hex()


def challenge_pattern(nonce, n_frames):
    """Deterministic pseudo-random on/off sequence for the strobe.

    We modulate within one 1.28s PMIC window: pattern alternates bursts.
    With n_frames ~8 at ~30fps the sequence covers ~0.27s of the window.
    """
    dig = hashlib.sha256(nonce.encode()).digest()
    # bits -> on/off for successive frame indices; ensure at least 2 lit
    bits = [bool(dig[i % len(dig)] >> (i % 8) & 1) for i in range(n_frames)]
    lit = sum(bits)
    if lit < 2:
        # force two lit frames at spread positions (deterministic from nonce)
        pos = [int.from_bytes(dig[0:2], "big") % n_frames,
               int.from_bytes(dig[2:4], "big") % n_frames]
        for p in pos:
            bits[p] = True
    return bits


class StrobeDriver:
    """Drives the flash LED through a challenge pattern while frames stream.

    The PMIC strobe latches (0->1 fires, stays until timeout), so the
    pattern is expressed as: fire strobe at 'on' transitions, and for 'off'
    stretches use torch=0 + wait. Torch brightness is too weak to matter
    for capture but forces the strobe channel dark via re-arm.
    """

    def __init__(self, strobe_path, brightness_path, pattern):
        self.sp = strobe_path
        self.bp = brightness_path
        self.pattern = pattern
        self.idx = 0

    def on_frame(self, frame_idx):
        """Apply strobe state for the frame about to be captured."""
        want = self.pattern[frame_idx % len(self.pattern)]
        if want:
            set_led(self.sp, 0)
            time.sleep(0.02)
            set_led(self.sp, 1)  # fire within the 1.28s window
        else:
            set_led(self.sp, 0)
        return want

    def off(self):
        set_led(self.sp, 0)
        if self.bp:
            set_led(self.bp, 0)


def _fmean(f):
    if hasattr(f, "mean"):
        try:
            return float(f.mean())
        except Exception:
            pass
    return sum(_frame_pixels(f)) / max(len(_frame_pixels(f)), 1)


def check_challenge(gray_frames, pattern):
    """Verify lit/dark correlation in the captured IR burst.

    gray_frames: list of 2-D uint8 arrays (the IR burst), aligned with
      pattern (same length).
    Returns (ok, detail) — never raises.
    """
    if len(gray_frames) != len(pattern) or not gray_frames:
        return False, {"reason": "length-mismatch",
                       "frames": len(gray_frames), "pattern": len(pattern)}
    means = [_fmean(f) for f in gray_frames]
    lit = [m for m, p in zip(means, pattern) if p]
    dark = [m for m, p in zip(means, pattern) if not p]
    detail = {"means": [round(m, 1) for m in means],
              "pattern": [int(p) for p in pattern]}
    if not lit or not dark:
        return False, {**detail, "reason": "pattern-degenerate"}
    # lit frames must be clearly brighter than dark frames
    gap = min(lit) - max(dark)
    detail["gap"] = round(gap, 1)
    if gap < 8.0:  # grayscale levels; tuned on strobe gap ~200 vs ~10
        return False, {**detail, "reason": "no-strobe-correlation"}
    return True, detail


def _frame_pixels(f):
    """Return pixel values as a flat list (numpy or list-backed frames)."""
    if hasattr(f, "ravel"):
        try:
            return [float(v) for v in f.ravel()]
        except Exception:
            pass
    if hasattr(f, "tolist"):
        return [float(v) for v in np.asarray(f).ravel().tolist()]
    return [float(v) for v in f]  # Frame(list) or any flat iterable


def temporal_noise(frames_gray):
    """Per-pixel temporal std across the burst, median-normalised.

    Real sensor: read/shot noise on every frame, plus photon noise under
    strobe. Prints: near-zero temporal delta. Screens: LCD refresh +
    codec quantisation artifacts, typically blocky and spatially uniform.
    Returns (score, detail); score 0..1-ish, higher = more sensor-like.
    Works with numpy arrays or any indexable 2-D frames.
    """
    if len(frames_gray) < 3:
        return 0.0, {"reason": "too-few-frames"}
    pix = [_frame_pixels(f) for f in frames_gray]
    n = min(len(p) for p in pix)
    if n == 0:
        return 0.0, {"reason": "empty-frames"}
    tstd = []
    for i in range(n):
        vals = [p[i] for p in pix]
        m = sum(vals) / len(vals)
        var = sum((v - m) ** 2 for v in vals) / len(vals)
        tstd.append(var ** 0.5)
    tstd.sort()
    med = tstd[len(tstd) // 2]
    bright = [f for f in frames_gray if _fmean(f) > 20.0]  # only lit count
    if len(bright) < 2:
        return 0.0, {"reason": "no-lit-pair"}
    bpix = [_frame_pixels(f) for f in bright]
    bn = min(len(p) for p in bpix)
    bstd = []
    for i in range(bn):
        vals = [p[i] for p in bpix]
        m = sum(vals) / len(vals)
        var = sum((v - m) ** 2 for v in vals) / len(vals)
        bstd.append(var ** 0.5)
    bstd.sort()
    bmed = bstd[len(bstd) // 2]
    score = 0.0
    if 0.8 <= bmed <= 30.0:
        score = 1.0
    elif 30.0 < bmed <= 60.0:
        score = 0.5  # noisy screen? partial credit, flag it
    detail = {"tstd_med": round(med, 2), "bstd_med": round(bmed, 2)}
    return score, detail



def fuse(rgb_res, ir_res, weights=None, both_required=True):
    """Weighted cross-modal fusion instead of OR.

    rgb_res/ir_res: (ok, sim, score) tuples (from verify attempt_dual).
    Returns (accepted, detail). With both_required=True, a photo attack
    that passes RGB but fails IR (or vice versa) is rejected.
    """
    w = weights or {"rgb": 0.5, "ir": 0.5}
    sims = {"rgb": rgb_res[1] if rgb_res else 0.0,
            "ir": ir_res[1] if ir_res else 0.0}
    oks = {"rgb": bool(rgb_res[0]) if rgb_res else False,
           "ir": bool(ir_res[0]) if ir_res else False}
    fused = w["rgb"] * sims["rgb"] + w["ir"] * sims["ir"]
    detail = {"sims": {k: round(v, 2) for k, v in sims.items()},
              "fused": round(fused, 2), "oks": oks}
    if both_required:
        accepted = oks["rgb"] and oks["ir"] and fused >= 0.36
    else:
        accepted = (oks["rgb"] or oks["ir"]) and fused >= 0.30
    return accepted, {**detail, "accepted": accepted}
