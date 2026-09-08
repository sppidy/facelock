"""Calibrated RGB/IR stereo. Sparse or ambiguous correspondence stays invalid."""
from collections import deque
import hashlib
import json
import os

import cv2
import numpy as np

from . import security


class PairCollector:
    """Pair lit IR with nearest RGB exposures, without reusing an RGB frame."""
    def __init__(self, max_skew_ms):
        self.limit = max_skew_ms * 1e6
        self.rgb = deque(maxlen=8)
        self.pending = deque(maxlen=3)
        self.last = {}

    def _validate(self, frame):
        previous = self.last.get(frame.source, (0, -1))
        if frame.timestamp_ns <= previous[0] or frame.sequence <= previous[1]:
            raise RuntimeError("stereo timestamps or sequences missing or out of order")
        self.last[frame.source] = (frame.timestamp_ns, frame.sequence)

    def add_rgb(self, frame, image):
        self._validate(frame)
        item = (image, frame.timestamp_ns)
        self.rgb.append(item)
        for pair in self.pending:
            if pair[1] is None or abs(item[1] - pair[0].timestamp_ns) < abs(pair[1][1] - pair[0].timestamp_ns):
                pair[1] = item

    def add_ir(self, frame):
        self._validate(frame)
        nearest = min(self.rgb, key=lambda p: abs(p[1] - frame.timestamp_ns), default=None)
        self.pending.append([frame, nearest])

    def finish(self):
        result, used = [], set()
        for ir, rgb in self.pending:
            if rgb is None or rgb[1] in used or abs(rgb[1] - ir.timestamp_ns) > self.limit:
                continue
            used.add(rgb[1])
            result.append((rgb[0], ir.image, rgb[1], ir.timestamp_ns))
        return result


def capture_identity(cfg):
    return {"cameras": cfg["cameras"], "runtime": cfg["runtime"],
            "rgb_size": [cfg["dual"]["rgb_width"], cfg["dual"]["rgb_height"]],
            "output_size": [cfg["capture"]["width"], cfg["capture"]["height"]],
            "resize": "fit-area-v1", "ir_led": cfg["ir_led"]}


def validate_calibration(data, cfg):
    if not isinstance(data, dict) or data.get("version") != 1 or data.get("capture") != capture_identity(cfg):
        raise ValueError("stereo calibration does not match capture settings")
    size = data.get("image_size")
    if not isinstance(size, list) or len(size) != 2 or any(type(v) is not int or not 160 <= v <= 1280 for v in size):
        raise ValueError("invalid stereo image size")
    ir_size = data.get("ir_size")
    if not isinstance(ir_size, list) or len(ir_size) != 2 or any(type(v) is not int or not 16 <= v <= 4096 for v in ir_size):
        raise ValueError("invalid IR image size")
    for key, shape in (("K1", (3, 3)), ("K2", (3, 3)), ("D1", (5,)), ("D2", (5,)),
                       ("R", (3, 3)), ("T", (3,))):
        a = np.asarray(data.get(key), dtype=float)
        if a.shape != shape or not np.isfinite(a).all():
            raise ValueError(f"invalid stereo {key}")
    for key in ("K1", "K2"):
        a = np.asarray(data[key])
        if a[0, 0] <= 0 or a[1, 1] <= 0 or not np.allclose(a[2], [0, 0, 1]):
            raise ValueError("invalid stereo intrinsics")
    r = np.asarray(data["R"])
    if not np.allclose(r.T @ r, np.eye(3), atol=1e-4) or not np.isclose(np.linalg.det(r), 1, atol=1e-4):
        raise ValueError("invalid stereo rotation")
    if not .001 <= np.linalg.norm(data["T"]) <= .2:
        raise ValueError("stereo baseline must be in metres, between 1 and 200 mm")
    rms = data.get("rms_px")
    if type(rms) not in (float, int) or not np.isfinite(rms) or not 0 <= rms <= 1:
        raise ValueError("stereo calibration RMS must be at most 1 pixel")
    return data


def load_calibration(cfg):
    path = cfg["depth"]["calibration"]
    if os.geteuid() == 0:
        security.trusted_path(path)
    blob = security.private_read(path, owner=os.geteuid(), max_bytes=65536)
    data = validate_calibration(json.loads(blob), cfg)
    return data, hashlib.sha256(blob).hexdigest()


class Reconstruction:
    def __init__(self, cfg):
        data, _ = load_calibration(cfg)
        self.size, self.ir_size = tuple(data["image_size"]), tuple(data["ir_size"])
        k1, d1, k2, d2, r, t = [np.asarray(data[k], dtype=float) for k in ("K1", "D1", "K2", "D2", "R", "T")]
        r1, r2, p1, p2, self.q, _, _ = cv2.stereoRectify(k1, d1, k2, d2, self.size, r, t.reshape(3, 1),
                                                       flags=cv2.CALIB_ZERO_DISPARITY, alpha=0)
        self.maps = [cv2.initUndistortRectifyMap(k, d, rot, p, self.size, cv2.CV_32FC1)
                     for k, d, rot, p in ((k1, d1, r1, p1), (k2, d2, r2, p2))]
        self.k, self.d, self.r1 = k1, d1, r1
        self.vertical = abs(p2[1, 3]) > abs(p2[0, 3])
        axis = 1 if self.vertical else 0
        # Include both baseline directions. Fail if the image cannot support
        # the disparity range needed at the configured closest distance.
        span = int(np.ceil(abs(p2[axis, 3]) / cfg["depth"]["min_distance_m"] / 16)) * 16 + 16
        if span * 2 + 16 >= self.size[axis]:
            raise ValueError("stereo baseline/range exceeds the calibrated image size")
        self.minimum = -span
        args = dict(minDisparity=-span, numDisparities=span * 2, blockSize=5,
                    P1=8 * 25, P2=32 * 25, disp12MaxDiff=1, uniquenessRatio=15,
                    speckleWindowSize=50, speckleRange=1, mode=cv2.STEREO_SGBM_MODE_SGBM)
        self.left, self.right = cv2.StereoSGBM_create(**args), cv2.StereoSGBM_create(**args)

    def compute(self, rgb, ir):
        if tuple(rgb.shape[1::-1]) != self.size or tuple(ir.shape[1::-1]) != self.ir_size:
            raise ValueError("stereo frame size differs from calibration")
        # Calibration uses IR resized to the RGB raster, with its own intrinsics.
        ir = cv2.resize(ir, self.size, interpolation=cv2.INTER_AREA)
        rectified = []
        for img, maps in zip((rgb, ir), self.maps):
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            gray = cv2.remap(gray, *maps, cv2.INTER_LINEAR)
            # Local contrast normalization helps cross-spectral correspondence;
            # it cannot manufacture matching texture when IR/RGB disagree.
            gray = cv2.createCLAHE(clipLimit=2, tileGridSize=(8, 8)).apply(gray)
            rectified.append(np.ascontiguousarray(gray.T if self.vertical else gray))
        left, right = rectified
        disp = self.left.compute(left, right).astype(np.float32) / 16
        reverse = self.right.compute(right, left).astype(np.float32) / 16
        yy, xx = np.indices(disp.shape)
        xr = np.rint(xx - disp).astype(int)
        inside = (xr >= 0) & (xr < disp.shape[1])
        backward = reverse[yy, np.clip(xr, 0, disp.shape[1] - 1)]
        valid = inside & (disp > self.minimum) & (backward > self.minimum) & (abs(disp + backward) <= 1)
        if self.vertical:
            disp, valid = disp.T.copy(), valid.T.copy()
        points = cv2.reprojectImageTo3D(disp, self.q) @ self.r1
        valid &= np.isfinite(points).all(axis=2) & (points[:, :, 2] > 0)
        points = points[valid]
        result = np.full(self.size[::-1], np.inf, dtype=np.float32)
        if points.size:
            xy, _ = cv2.projectPoints(points, np.zeros(3), np.zeros(3), self.k, self.d)
            xy = np.rint(xy.reshape(-1, 2)).astype(int)
            inside = (xy[:, 0] >= 0) & (xy[:, 0] < self.size[0]) & (xy[:, 1] >= 0) & (xy[:, 1] < self.size[1])
            xy, points = xy[inside], points[inside]
            np.minimum.at(result, (xy[:, 1], xy[:, 0]), points[:, 2])
        result[~np.isfinite(result)] = 0
        return result
