"""Frame quality gates for face-unlock captures (stdlib + cv2 + numpy)."""
import cv2
import numpy as np


def _face_region(img_bgr, det_box, padding=0.12):
    """Return a padded face crop, or the full frame for an invalid box."""
    if det_box is None:
        return img_bgr
    x, y, w, h = (float(v) for v in det_box[:4])
    if not all(np.isfinite((x, y, w, h))) or w <= 1 or h <= 1:
        return img_bgr
    ih, iw = img_bgr.shape[:2]
    pad_x, pad_y = w * padding, h * padding
    x0 = max(0, int(round(x - pad_x)))
    y0 = max(0, int(round(y - pad_y)))
    x1 = min(iw, int(round(x + w + pad_x)))
    y1 = min(ih, int(round(y + h + pad_y)))
    crop = img_bgr[y0:y1, x0:x1]
    return crop if crop.shape[0] >= 8 and crop.shape[1] >= 8 else img_bgr


def blur_score(img_bgr, det_box=None):
    """Laplacian variance proxy, measured on the face when available.

    A dark/flat IR background used to dominate the old full-frame score even
    when the detected face itself was sharp.  The small padded crop keeps a
    little context without allowing the unused border to fail the gate.
    """
    gray = cv2.cvtColor(_face_region(img_bgr, det_box), cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def brightness(img_bgr, det_box=None):
    """Mean brightness of the face crop when a detector box is available."""
    return float(np.asarray(_face_region(img_bgr, det_box)).mean())


def inspect(img_bgr, det_box=None, cfg=None):
    """Return quality status, reasons, and measured blur/brightness."""
    cfg = cfg or {}
    reasons = {}
    b = blur_score(img_bgr, det_box)
    m = brightness(img_bgr, det_box)
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
    return not reasons, reasons, b, m


def check(img_bgr, det_box=None, cfg=None):
    """Returns (ok, reasons_dict). det_box = (x, y, w, h) or None."""
    ok, reasons, _, _ = inspect(img_bgr, det_box, cfg)
    return ok, reasons
