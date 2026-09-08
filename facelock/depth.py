"""Experimental measured-depth face relief gate, not validated anti-spoofing.

Input is metric depth registered to the exact RGB image being recognized.
Removing a fitted plane prevents a tilted flat surface passing on range alone.
No monocular depth estimates or missing-data interpolation are used.
"""
import math

import numpy as np


def check(depth_m, image_shape, box, cfg):
    def reject(reason, **detail):
        return False, {"ok": False, "reason": reason, **detail}

    if depth_m is None:
        return reject("missing-depth")
    a = np.asarray(depth_m)
    if a.ndim != 2 or a.shape != tuple(image_shape[:2]) or a.dtype.kind not in "fiu":
        return reject("unaligned-depth")
    if box is None or len(box) < 4 or not all(math.isfinite(float(v)) for v in box[:4]):
        return reject("no-depth-face")
    x, y, w, h = map(float, box[:4])
    ih, iw = a.shape
    if w < 20 or h < 20 or x < 0 or y < 0 or x + w > iw or y + h > ih:
        return reject("depth-face-out-of-bounds")
    # Interior only: exclude hair, face edges and background discontinuities.
    roi = a[math.ceil(y + .2 * h):int(y + .8 * h),
            math.ceil(x + .2 * w):int(x + .8 * w)].astype(np.float64)
    valid = np.isfinite(roi) & (roi >= cfg["min_distance_m"]) & (roi <= cfg["max_distance_m"])
    coverage = float(valid.mean()) if roi.size else 0.0
    if coverage < cfg["min_valid_fraction"] or valid.sum() < 100:
        return reject("insufficient-depth", valid_fraction=round(coverage, 3))
    # Sample a bounded spatial grid for predictable per-frame work.
    stride = max(1, math.ceil(max(roi.shape) / 64))
    roi, valid = roi[::stride, ::stride], valid[::stride, ::stride]
    yy, xx = np.indices(roi.shape, dtype=float)
    xx = (xx / max(roi.shape[1] - 1, 1) - .5) * 2
    yy = (yy / max(roi.shape[0] - 1, 1) - .5) * 2
    # Inverse axial depth of a plane is affine in image coordinates.
    design = np.column_stack((xx[valid], yy[valid], np.ones(valid.sum())))
    coeff, _, rank, _ = np.linalg.lstsq(design, 1.0 / roi[valid], rcond=None)
    if rank != 3:
        return reject("degenerate-depth")
    inverse_plane = coeff[0] * xx + coeff[1] * yy + coeff[2]
    if np.any(inverse_plane <= 0):
        return reject("invalid-depth-plane")
    residual = roi - 1.0 / inverse_plane
    p10, p90 = np.percentile(residual[valid], [10, 90])
    relief = float(p90 - p10)
    centre = (abs(xx) < .35) & (abs(yy) < .5)
    left = xx < -.55
    right = xx > .55
    regions = (centre, left, right)
    if any((valid & r).sum() < 10 or (valid & r).sum() / max(r.sum(), 1) < cfg["min_valid_fraction"]
           for r in regions):
        return reject("insufficient-depth-regions")
    nose, cheek_l, cheek_r = [float(np.median(residual[valid & r])) for r in regions]
    protrusion = min(cheek_l - nose, cheek_r - nose)
    detail = {"valid_fraction": round(coverage, 3), "relief_m": round(relief, 4),
              "protrusion_m": round(protrusion, 4)}
    if not cfg["min_relief_m"] <= relief <= cfg["max_relief_m"]:
        return reject("depth-relief-out-of-range", **detail)
    if protrusion < cfg["min_protrusion_m"]:
        return reject("no-convex-face-depth", **detail)
    return True, {"ok": True, "reason": "ok", **detail}
