import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from facelock import acquisition, config, liveness, recognize
from facelock.dual_capture import CameraSession, Frame, _read_plane, decode_rgb
from test_auth import settings, vector


class LivenessTests(unittest.TestCase):
    def test_balanced_nonce_patterns(self):
        self.assertEqual(liveness.challenge_pattern("a"), liveness.challenge_pattern("a"))
        self.assertNotEqual(liveness.make_nonce(), liveness.make_nonce())
        for count in (6, 8, 16, 32):
            self.assertEqual(sum(liveness.challenge_pattern("a", count)), count // 2)
        for count in (1, 7, 34, 8.0, True):
            with self.assertRaises(ValueError):
                liveness.challenge_pattern("a", count)

    def test_correlation_rejects_wrong_labels_and_invalid_frames(self):
        pattern = [0, 1] * 4
        frames = [np.full((4, 4), 150 if lit else 8) for lit in pattern]
        self.assertTrue(liveness.check_challenge(frames, pattern)[0])
        self.assertFalse(liveness.check_challenge(frames[1:] + frames[:1], pattern)[0])
        self.assertFalse(liveness.check_challenge(frames, [1] * 8)[0])
        self.assertFalse(liveness.check_challenge(frames, [2] * 8)[0])
        self.assertFalse(liveness.check_challenge([np.full((4, 4), np.nan)] * 8, pattern)[0])
        self.assertFalse(liveness.check_challenge(frames, pattern, roi=(99, 99, 1, 1))[0])

    def test_temporal_noise_is_diagnostic_only(self):
        result = liveness.temporal_noise([np.full((4, 4), 120)] * 4)
        self.assertEqual(result["use"], "diagnostic-only")
        self.assertEqual(result["std_median"], 0)
        self.assertNotIn("ok", result)

    def test_torch_cleanup_on_exception(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            (p / "max_brightness").write_text("255")
            (p / "brightness").write_text("0")
            (p / "flash_strobe").write_text("1")
            with self.assertRaises(RuntimeError):
                with liveness.TorchDriver(p / "brightness", 128, p / "flash_strobe") as led:
                    led.set(True)
                    self.assertEqual((p / "brightness").read_text(), "128")
                    raise RuntimeError("camera failed")
            self.assertEqual((p / "brightness").read_text(), "0")
            self.assertEqual((p / "flash_strobe").read_text(), "0")

    def test_flash_rearms_without_changing_current_or_timeout(self):
        with tempfile.TemporaryDirectory() as temp, patch("facelock.liveness.time.sleep"):
            p = Path(temp)
            for name, value in {"brightness": "0", "flash_strobe": "0",
                                "flash_timeout": "1280000", "flash_brightness": "700000"}.items():
                (p / name).write_text(value)
            with liveness.FlashDriver(p / "brightness", 128, p / "flash_strobe") as led:
                led.set(True)
                self.assertEqual((p / "flash_strobe").read_text(), "1")
                led.set(False)
                self.assertEqual((p / "flash_strobe").read_text(), "0")
                led.set(True)
                led.expires = time.monotonic() - 1
                with self.assertRaises(RuntimeError):
                    led.check()
            self.assertEqual((p / "flash_strobe").read_text(), "0")
            self.assertEqual((p / "flash_brightness").read_text(), "700000")
            self.assertEqual((p / "flash_timeout").read_text(), "1280000")


class PhaseTests(unittest.TestCase):
    def test_transition_frames_are_not_mislabeled(self):
        now = [1_000_000_000]
        states = []
        driver = SimpleNamespace(set=states.append)
        collector = acquisition.PhaseCollector([0, 1], driver, clock=lambda: now[0])
        def add(seq, ms):
            timestamp = 1_000_000_000 + ms * 1_000_000
            now[0] = timestamp
            img = np.full((2, 2, 3), seq, np.uint8)
            collector.add(Frame("ir", img, timestamp, seq, None, None))
        for seq, ms in enumerate((10, 20, 90, 120, 150), 1):
            add(seq, ms)
        self.assertEqual(collector.flags, [0, 0])
        self.assertEqual(states, [0, 1])
        for seq, ms in enumerate((160, 180, 220, 260, 280), 6):
            add(seq, ms)
        self.assertTrue(collector.done)
        self.assertEqual(collector.flags, [0, 0, 1, 1])
        self.assertEqual([int(f[0, 0]) for f in collector.frames], [4, 5, 9, 10])

    def test_out_of_order_timestamp_or_sequence_rejected(self):
        for timestamp, sequence in ((10, 2), (20, 1)):
            p = acquisition.PhaseCollector([0, 1], SimpleNamespace(set=lambda _: None), clock=lambda: 0)
            p.add(Frame("ir", np.zeros((2, 2, 3), np.uint8), 10, 1, None, None))
            with self.assertRaises(RuntimeError):
                p.add(Frame("ir", np.zeros((2, 2, 3), np.uint8), timestamp, sequence, None, None))

    def test_start_failure_extinguishes_illumination(self):
        calls = []
        class Driver:
            def __enter__(self):
                calls.append("led-enter")
                return self
            def __exit__(self, *exc):
                calls.append("led-off")
        class FailedSession:
            def __init__(self, *args):
                pass
            def __enter__(self):
                raise RuntimeError("busy")
            def __exit__(self, *exc):
                pass
        with self.assertRaises(RuntimeError):
            acquisition.capture(settings(), session_factory=FailedSession, driver_factory=lambda _: Driver())
        self.assertEqual(calls, ["led-enter", "led-off"])


class PixelTests(unittest.TestCase):
    def test_resize_preserves_entire_scene_and_aspect_ratio(self):
        image = np.zeros((1080, 1920, 3), np.uint8)
        image[:, :300] = (255, 0, 0)
        image[:, -300:] = (0, 0, 255)
        small = acquisition.fit_frame(image, 640, 480)
        self.assertEqual(small.shape, (360, 640, 3))
        np.testing.assert_array_equal(small[180, 0], (255, 0, 0))
        np.testing.assert_array_equal(small[180, -1], (0, 0, 255))
        self.assertIs(acquisition.fit_frame(small, 640, 480), small)

    def test_word_order_and_row_padding(self):
        raw = bytes([10, 20, 30, 255, 40, 50, 60, 255, 9, 9, 9, 9])
        np.testing.assert_array_equal(decode_rgb(raw, 2, 1, 12, "ABGR8888"), [[[30, 20, 10], [60, 50, 40]]])
        np.testing.assert_array_equal(decode_rgb(raw, 2, 1, 12, "ARGB8888"), [[[10, 20, 30], [40, 50, 60]]])
        with self.assertRaises(ValueError):
            decode_rgb(raw[:4], 2, 1, 12, "ABGR8888")
        with self.assertRaises(ValueError):
            decode_rgb(raw, 2, 1, 12, "unknown")

    def test_unaligned_plane_offset(self):
        with tempfile.TemporaryFile() as f:
            f.write(b"paddingDATAtrailing")
            f.flush()
            plane = SimpleNamespace(fd=f.fileno(), offset=7, length=4)
            self.assertEqual(_read_plane(plane), b"DATA")

    def test_read_copies_before_request_reuse(self):
        data = bytearray([10, 20, 30, 255, 40, 50, 60, 255])
        md = SimpleNamespace(status=0, timestamp=100, sequence=1,
                             planes=[SimpleNamespace(bytes_used=8)])
        fb = SimpleNamespace(metadata=md, planes=[None])
        req = SimpleNamespace(cookie=1, status=1, Status=SimpleNamespace(Complete=1),
                              metadata={}, reuse=lambda: data.__setitem__(slice(None), bytes(8)))
        queued = []
        session = CameraSession({"rgb": "test"})
        session.event_fd = 10
        session.cm = SimpleNamespace(get_ready_requests=lambda: [req])
        session.libcamera = SimpleNamespace(FrameMetadata=SimpleNamespace(Status=SimpleNamespace(Success=0)))
        session.requests = {1: ("rgb", fb, req)}
        session.jobs = {"rgb": {"cam": SimpleNamespace(queue_request=queued.append), "format": "ABGR8888",
                                "sc": SimpleNamespace(size=SimpleNamespace(width=2, height=1), stride=8)}}
        with patch("facelock.dual_capture.select.select", return_value=([10], [], [])), \
             patch("facelock.dual_capture._read_plane", side_effect=lambda _: data):
            frames = session.read(time.monotonic() + 1)
        np.testing.assert_array_equal(frames[0].image, [[[30, 20, 10], [60, 50, 40]]])
        self.assertEqual(queued, [req])


class RecognitionTests(unittest.TestCase):
    def test_detector_uses_configured_threshold_and_rejects_multiple_faces(self):
        face = np.array([1, 2, 8, 8, *([0] * 10), 0.75])
        from unittest.mock import Mock
        det, rec = Mock(), Mock()
        det.detect.return_value = (None, [face])
        rec.feature.return_value = vector().reshape(1, -1)
        v, score, box = recognize.embed(np.zeros((16, 16, 3), np.uint8), det, rec, 0.6)
        det.setScoreThreshold.assert_called_once_with(0.6)
        self.assertIsNotNone(v)
        det.detect.return_value = (None, [face, face])
        self.assertIsNone(recognize.embed(np.zeros((16, 16, 3), np.uint8), det, rec, 0.6)[0])

    @staticmethod
    def attention_image(pupil_shift=(0, 0)):
        image = np.full((120, 120, 3), 190, np.uint8)
        yy, xx = np.indices(image.shape[:2])
        for cx, cy in ((45, 50), (75, 50)):
            mask = ((xx - cx - pupil_shift[0]) ** 2 +
                    (yy - cy - pupil_shift[1]) ** 2) <= 3 ** 2
            image[mask] = 15
        return image

    @staticmethod
    def frontal_face():
        return np.array([20, 15, 80, 100, 45, 50, 75, 50, 60, 68,
                         48, 88, 72, 88, 0.95], dtype=np.float64)

    def test_attention_accepts_frontal_face_and_centered_pupils(self):
        detail = recognize.check_attention(
            self.attention_image(), self.frontal_face(), config.DEFAULTS["attention"])
        self.assertTrue(detail["ok"], detail)
        self.assertEqual(detail["reason"], "ok")

    def test_attention_rejects_turned_face_and_away_pupils(self):
        face = self.frontal_face()
        face[8] = 70  # nose displaced from the eye midpoint
        detail = recognize.check_attention(
            self.attention_image(), face, config.DEFAULTS["attention"])
        self.assertEqual(detail["reason"], "head-turned")
        detail = recognize.check_attention(
            self.attention_image((4, 0)), self.frontal_face(), config.DEFAULTS["attention"])
        self.assertEqual(detail["reason"], "eyes-looking-away")

    def test_attention_rejects_missing_eye_contrast(self):
        detail = recognize.check_attention(
            np.full((120, 120, 3), 100, np.uint8), self.frontal_face(),
            config.DEFAULTS["attention"])
        self.assertEqual(detail["reason"], "eyes-not-visible")
