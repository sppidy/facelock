import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import calibrate
from facelock import acquisition, auth, config, depth, stereo
from facelock.acquisition import Burst
from facelock.dual_capture import Frame
from test_auth import settings, vector


def face_depth(size=100):
    y, x = np.mgrid[-1:1:complex(size), -1:1:complex(size)]
    return .6 - .055 * np.exp(-((x / .4) ** 2 + (y / .7) ** 2))


def calibration(cfg, vertical=False):
    return {"version": 1, "capture": stereo.capture_identity(cfg), "image_size": [320, 240],
            "ir_size": [320, 240], "K1": [[250, 0, 160], [0, 250, 120], [0, 0, 1]],
            "K2": [[250, 0, 160], [0, 250, 120], [0, 0, 1]], "D1": [0.] * 5, "D2": [0.] * 5,
            "R": np.eye(3).tolist(), "T": [0, -.02, 0] if vertical else [-.02, 0, 0], "rms_px": .2}


class DepthTests(unittest.TestCase):
    def check(self, a, box=(0, 0, 100, 100)):
        return depth.check(a, (100, 100, 3), box, config.DEFAULTS["depth"])[0]

    def test_convex_relief_passes_but_flat_tilted_and_concave_fail(self):
        self.assertTrue(self.check(face_depth()))
        y, x = np.mgrid[:100, :100]
        for a in (np.full((100, 100), .6), 1 / (1.5 + .004 * x + .001 * y),
                  1.2 - face_depth()):
            self.assertFalse(self.check(a))

    def test_invalid_missing_sparse_and_out_of_range_fail(self):
        for a in (None, np.ones((20, 20)), np.full((100, 100), np.nan),
                  np.zeros((100, 100)), face_depth() + 2, face_depth() * .1):
            self.assertFalse(self.check(a))
        a = face_depth()
        a[::2] = 0
        self.assertFalse(self.check(a))
        self.assertFalse(self.check(face_depth(), (-1, 0, 100, 100)))

    def test_missing_central_region_cannot_be_hidden_by_global_coverage(self):
        a = face_depth()
        a[35:65, 42:58] = 0
        self.assertFalse(self.check(a))

    def test_both_enrollment_and_verification_gate_matching_rgb_frames(self):
        cfg = settings()
        cfg["depth"].update(enabled=True, calibration="/unused.json")
        texture = (np.indices((100, 100)).sum(axis=0) % 2 * 60 + 70).astype(np.uint8)
        img = np.repeat(texture[:, :, None], 3, axis=2)
        b = Burst({"rgb": [img] * 3, "ir": [img] * 3}, [], [], {}, [face_depth()] * 3)
        # Isolate measured depth from the independent existing illumination gate.
        with patch("facelock.auth.liveness.temporal_noise", return_value={}), \
             patch("facelock.auth.recognize.embed", return_value=(vector(), .9, (0, 0, 100, 100))), \
             patch("facelock.auth.enrollment_metadata", return_value={}), \
             patch("facelock.auth.liveness.check_challenge", return_value=(True, {})):
            b.ir_frames, b.pattern = [np.ones((100, 100))] * 6, [0, 1] * 3
            for count, expected in ((3, True), (2, True), (1, False), (0, False)):
                b.depth_frames = [face_depth() if i < count else np.zeros((100, 100)) for i in range(3)]
                kw = {"capture_fn": lambda _: b, "models_fn": lambda _: (None, None)}
                self.assertEqual(auth.verify(cfg, {"rgb": vector(), "ir": vector()}, {}, **kw)[0], expected)
                self.assertEqual(auth.enroll(cfg, **kw)[0] is not None, expected)
            b.depth_frames = [face_depth()]  # Never recycle a map for another frame.
            self.assertFalse(auth.verify(cfg, {"rgb": vector(), "ir": vector()}, {}, **kw)[0])


class PairTests(unittest.TestCase):
    def frame(self, source, ms, sequence=1):
        return Frame(source, np.zeros((10, 10, 3), np.uint8), int(ms * 1e6), sequence, None, None)

    def test_nearest_future_rgb_is_selected_without_reusing_frames(self):
        pairs = stereo.PairCollector(10)
        a = self.frame("rgb", 100)
        pairs.add_rgb(a, a.image)
        pairs.add_ir(self.frame("ir", 109))
        b = self.frame("rgb", 110, 2)
        pairs.add_rgb(b, b.image)
        pairs.add_ir(self.frame("ir", 111, 2))
        result = pairs.finish()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][2], b.timestamp_ns)

    def test_missing_unsynchronized_and_repeated_frames_reject(self):
        pairs = stereo.PairCollector(10)
        pairs.add_ir(self.frame("ir", 100))
        self.assertEqual(pairs.finish(), [])
        a = self.frame("rgb", 120)
        pairs.add_rgb(a, a.image)
        self.assertEqual(pairs.finish(), [])
        with self.assertRaises(RuntimeError):
            pairs.add_rgb(a, a.image)

    def test_capture_reconstructs_only_paired_lit_frames_and_closes_devices(self):
        cfg = settings()
        cfg["depth"].update(enabled=True, calibration="/unused.json")
        cfg["dual"]["settle"].update(min_frames=3)
        cfg["capture"]["warmup_sec"] = .2
        now, led, closed = [1.0], [False], []

        class Driver:
            def __enter__(self):
                return self
            def set(self, enabled):
                led[0] = bool(enabled)
            def __exit__(self, *args):
                closed.append("led")
                led[0] = False

        class Session:
            sequence = 0
            def __init__(self, *args):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                closed.append("camera")
            def read(self, deadline):
                now[0] += .1
                self.sequence += 1
                if now[0] >= deadline:
                    raise TimeoutError("test capture exceeded its deadline")
                rgb = np.full((240, 320, 3), self.sequence, np.uint8)
                ir = np.full_like(rgb, 150 if led[0] else 10)
                return [Frame("rgb", rgb, int(now[0] * 1e9), self.sequence, None, None),
                        Frame("ir", ir, int(now[0] * 1e9) + 1_000_000, self.sequence, None, None)]

        collector = acquisition.PhaseCollector
        with patch("facelock.acquisition.time.monotonic", side_effect=lambda: now[0]), \
             patch("facelock.acquisition.PhaseCollector",
                   side_effect=lambda *args: collector(*args, clock=lambda: int(now[0] * 1e9))), \
             patch("facelock.stereo.Reconstruction") as reconstruction:
            reconstruction.return_value.compute.side_effect = lambda rgb, ir: np.full(rgb.shape[:2], .6)
            result = acquisition.capture(cfg, session_factory=Session, driver_factory=lambda _: Driver())
        self.assertEqual(closed, ["camera", "led"])
        self.assertEqual(len(result.depth_frames), 3)
        self.assertEqual(len(result.images["rgb"]), 3)
        self.assertEqual(reconstruction.return_value.compute.call_count, 3)
        self.assertTrue(all(np.all(img == 150) for img in result.images["ir"]))
        self.assertEqual(result.stats["stereo"]["skew_ms"], [1., 1., 1.])
        self.assertEqual(len({p[2] for p in result.stereo_pairs}), 3)


class CalibrationTests(unittest.TestCase):
    def test_depth_requires_calibration_and_concurrent_both_sensors(self):
        cfg = settings()
        self.assertFalse(cfg["depth"]["enabled"])
        for updates in ({"depth": {"enabled": True}}, {"depth": {"enabled": "yes"}},
                        {"depth": {"enabled": True, "calibration": "/a.json"}, "capture": {"mode": "sequential"}},
                        {"depth": {"max_skew_ms": float("nan")}}):
            with self.assertRaises(ValueError):
                config.validate(config.merge(cfg, updates))

    def test_calibration_validation_and_content_bound_metadata(self):
        cfg = settings()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stereo.json"
            cfg["depth"].update(enabled=True, calibration=str(path))
            data = calibration(cfg)
            path.write_text(json.dumps(data))
            path.chmod(0o600)
            with patch("facelock.stereo.security.trusted_path"):
                first = auth.enrollment_metadata(cfg)
                data["T"][0] = -.021
                path.write_text(json.dumps(data))
                self.assertNotEqual(first, auth.enrollment_metadata(cfg))
                path.chmod(0o644)
                with self.assertRaises(PermissionError):
                    stereo.load_calibration(cfg)
            data["rms_px"] = 2
            with self.assertRaises(ValueError):
                stereo.validate_calibration(data, cfg)
            data["rms_px"] = .2
            data["capture"] = {}
            with self.assertRaises(ValueError):
                stereo.validate_calibration(data, cfg)

    def test_reconstruction_recovers_metric_depth_horizontal_and_vertical(self):
        cfg = settings()
        for vertical in (False, True):
            data = calibration(cfg, vertical)
            with patch("facelock.stereo.load_calibration", return_value=(data, "digest")):
                model = stereo.Reconstruction(cfg)
            rng = np.random.default_rng(10)
            gray = rng.integers(0, 256, (240, 320), dtype=np.uint8)
            # f * baseline / disparity = 250 * .02 / 10 = .5 m.
            ir = np.roll(gray, -10, axis=0 if vertical else 1)
            rgb = np.repeat(gray[:, :, None], 3, axis=2)
            ir = np.repeat(ir[:, :, None], 3, axis=2)
            measured = model.compute(rgb, ir)
            valid = measured[measured > 0]
            self.assertGreater(valid.size, measured.size * .3)
            self.assertAlmostEqual(float(np.median(valid)), .5, delta=.02)
            with self.assertRaises(ValueError):
                model.compute(rgb[:100], ir)
            self.assertFalse(np.any(model.compute(np.zeros_like(rgb), np.zeros_like(ir))))

    def test_calibration_requires_enough_pairs_and_metric_square_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "at least 15"):
                calibrate.calibrate(tmp, settings(), 9, 6, .02)
            with self.assertRaises(ValueError):
                calibrate.calibrate(tmp, settings(), 9, 6, 20)
