"""Frame quality gates for face-unlock captures (stdlib + cv2 + numpy)."""
import cv2
import numpy as np


def blur_score(img_bgr):
    """Laplacian variance proxy. Higher = sharper. ~<30 is visibly soft."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def brightness(img_bgr):
    return float(np.asarray(img_bgr).mean())


def check(img_bgr, det_box=None, cfg=None):
    """Returns (ok, reasons_dict). det_box = (x, y, w, h) or None."""
    cfg = cfg or {}
    reasons = {}
    b = blur_score(img_bgr)
    m = brightness(img_bgr)
    min_blur = float(cfg.get("min_blur", 25.0))
    lo, hi = cfg.get("brightness_range", [15.0, 235.0])
    min_frac = float(cfg.get("min_face_frac", 0.05))
    if b < min_blur:
        reasons["blur"] = round(b, 1)
    if not (lo <= m <= hi):
        reasons["brightness"] = round(m, 1)
    if det_box is not None:
        x, y, w, h = (int(v) for v in det_box[:4])
        ih, iw = img_bgr.shape[:2]
        frac = (w * h) / max(iw * ih, 1)
        if frac < min_frac:
            reasons["face_frac"] = round(frac, 3)
    return (not reasons), reasons
