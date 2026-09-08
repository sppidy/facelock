#!/usr/bin/env python3
"""Fit RGB/IR stereo calibration from diagnose.py --save-stereo-pair captures."""
import argparse
import json
import math
import os
from pathlib import Path

import cv2
import numpy as np

from facelock import config, stereo


def calibrate(directory, cfg, columns, rows, square_m):
    if not 3 <= columns <= 20 or not 3 <= rows <= 20 or not math.isfinite(square_m) or not .001 <= square_m <= .1:
        raise ValueError("use 3..20 inner corners per side and square size in metres (0.001..0.1)")
    if cfg["capture"]["mode"] != "dual-pycamera" or set(cfg["auth"]["required_sensors"]) != {"rgb", "ir"}:
        raise ValueError("calibration requires the concurrent RGB/IR profile")
    grid = np.zeros((columns * rows, 3), np.float32)
    grid[:, :2] = np.mgrid[:columns, :rows].T.reshape(-1, 2) * square_m
    objects, rgb_points, ir_points = [], [], []
    size = ir_size = None
    seen = set()
    for manifest in sorted(Path(directory).glob("pair-*/capture.json")):
        data = json.loads(manifest.read_text())
        if data.get("capture") != stereo.capture_identity(cfg):
            raise ValueError("calibration dataset mixes camera configurations")
        times = data.get("rgb_ns"), data.get("ir_ns")
        if any(type(t) is not int or t <= 0 for t in times) or abs(times[0] - times[1]) > cfg["depth"]["max_skew_ms"] * 1e6:
            raise ValueError("calibration pair lacks synchronized timestamps")
        if times in seen:
            raise ValueError("duplicate calibration exposure")
        seen.add(times)
        rgb = cv2.imread(str(manifest.parent / "rgb.png"), cv2.IMREAD_GRAYSCALE)
        ir = cv2.imread(str(manifest.parent / "ir.png"), cv2.IMREAD_GRAYSCALE)
        if rgb is None or ir is None:
            raise ValueError("missing calibration image")
        current, current_ir = tuple(rgb.shape[::-1]), tuple(ir.shape[::-1])
        if size is not None and (current != size or current_ir != ir_size):
            raise ValueError("calibration dataset mixes image sizes")
        size, ir_size = current, current_ir
        ir = cv2.resize(ir, size, interpolation=cv2.INTER_AREA)
        found1, corners1 = cv2.findChessboardCornersSB(rgb, (columns, rows))
        found2, corners2 = cv2.findChessboardCornersSB(ir, (columns, rows))
        if found1 and found2:
            objects.append(grid.copy())
            rgb_points.append(corners1)
            ir_points.append(corners2)
    if len(objects) < 15:
        raise ValueError(f"need at least 15 pairs with the board visible in both spectra; found {len(objects)}")
    _, k1, d1, _, _ = cv2.calibrateCamera(objects, rgb_points, size, None, None)
    _, k2, d2, _, _ = cv2.calibrateCamera(objects, ir_points, size, None, None)
    rms, k1, d1, k2, d2, r, t, _, _ = cv2.stereoCalibrate(
        objects, rgb_points, ir_points, k1, d1, k2, d2, size, flags=cv2.CALIB_FIX_INTRINSIC,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 100, 1e-7))
    result = {"version": 1, "capture": stereo.capture_identity(cfg), "image_size": list(size),
              "ir_size": list(ir_size), "rms_px": float(rms), "pairs": len(objects),
              "square_m": square_m}
    result.update({k: a.tolist() for k, a in (("K1", k1), ("K2", k2), ("D1", d1.ravel()),
                                            ("D2", d2.ravel()), ("R", r), ("T", t.ravel()))})
    return stereo.validate_calibration(result, cfg)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("directory", type=Path)
    ap.add_argument("output", type=Path, help="new calibration JSON file; existing files are not overwritten")
    ap.add_argument("--config", default="/etc/facelock/config.yaml")
    ap.add_argument("--columns", type=int, required=True, help="checkerboard inner corners")
    ap.add_argument("--rows", type=int, required=True)
    ap.add_argument("--square-m", type=float, required=True, help="measured square edge length in metres")
    args = ap.parse_args()
    result = calibrate(args.directory, config.load(args.config), args.columns, args.rows, args.square_m)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(result, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    print(f"Saved {result['pairs']} pairs, RMS {result['rms_px']:.3f} px to {args.output}")


if __name__ == "__main__":
    main()
