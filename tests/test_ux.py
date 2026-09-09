import io
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np

import enroll
from facelock import auth, cli, drift, feedback, store, wizard
from facelock.enrollment_ui import EnrollmentProcess
from test_auth import settings, vector, burst


class WizardTests(unittest.TestCase):
    def setUp(self):
        self.cfg = settings()
        self.vectors = {s: vector() for s in ("rgb", "ir")}

    def test_retries_rejections_and_keeps_three_consistent_bursts(self):
        capture = Mock(side_effect=[(None, {"reason": "blur"}), (self.vectors, {}),
                                   (self.vectors, {}), (self.vectors, {})])
        events = []
        result, detail = wizard.collect(self.cfg, events.append, enroll_fn=capture,
                                        models_fn=lambda _: (None, None))
        self.assertEqual(detail["collected"], 3)
        self.assertEqual(capture.call_count, 4)
        np.testing.assert_allclose(result["rgb"], vector())
        self.assertEqual([e["collected"] for e in events if e["event"] == "burst"], [0, 1, 2, 3])

    def test_capture_errors_and_other_faces_do_not_count(self):
        other = {s: vector(1) for s in ("rgb", "ir")}
        capture = Mock(side_effect=[RuntimeError("busy"), (self.vectors, {}),
                                   (other, {}), (self.vectors, {}), (self.vectors, {})])
        result, _ = wizard.collect(self.cfg, lambda _: None, enroll_fn=capture,
                                   models_fn=lambda _: (None, None))
        np.testing.assert_allclose(result["ir"], vector())
        self.assertEqual(capture.call_count, 5)

    def test_exhausted_retries_never_return_partial_enrollment(self):
        capture = Mock(return_value=(None, {"reason": "no-face"}))
        refs, detail = wizard.collect(self.cfg, lambda _: None, enroll_fn=capture,
                                      models_fn=lambda _: (None, None))
        self.assertIsNone(refs)
        self.assertEqual(detail["reason"], "not-enough-quality-samples")
        self.assertEqual(capture.call_count, 5)

    def test_termination_stops_retries_immediately(self):
        capture = Mock(side_effect=cli.AttemptTerminated("terminated"))
        with self.assertRaises(cli.AttemptTerminated):
            wizard.collect(self.cfg, lambda _: None, enroll_fn=capture,
                           models_fn=lambda _: (None, None))
        self.assertEqual(capture.call_count, 1)

    def test_capture_timeout_can_be_retried(self):
        capture = Mock(side_effect=[TimeoutError("camera timeout"),
                                   (self.vectors, {}), (self.vectors, {}),
                                   (self.vectors, {})])
        result, detail = wizard.collect(self.cfg, lambda _: None, enroll_fn=capture,
                                        models_fn=lambda _: (None, None))
        self.assertIsNotNone(result)
        self.assertEqual(detail["collected"], 3)

    def test_cancellation_drops_collected_samples(self):
        capture = Mock(return_value=(self.vectors, {}))
        refs, detail = wizard.collect(self.cfg, lambda _: None, enroll_fn=capture,
                                      models_fn=lambda _: (None, None),
                                      cancelled=Mock(side_effect=[False, True]))
        self.assertIsNone(refs)
        self.assertEqual(detail["reason"], "enrollment-cancelled")
        self.assertEqual(capture.call_count, 1)

    def test_every_frame_reports_score_blur_and_brightness(self):
        with patch("facelock.recognize.embed",
                   return_value=(vector(), 0.9, (0, 0, 32, 32), None)):
            _, detail = auth.extract(self.cfg, None, None, burst())
        for source in detail["sources"].values():
            for sample in source["samples"]:
                self.assertGreater(sample["blur"], 0)
                self.assertEqual(sample["score"], 0.9)
                self.assertGreater(sample["mean"], 0)

    def test_saving_requires_explicit_confirmation(self):
        from contextlib import nullcontext
        with tempfile.TemporaryDirectory() as tmp:
            self.cfg["store"]["dir"] = tmp
            for consent in (False, True):
                with self.subTest(consent=consent), patch("enroll.cli.load_config", return_value=self.cfg), \
                     patch("enroll.cli.account", return_value="alice"), \
                     patch("enroll.cli.attempt", return_value=nullcontext()), \
                     patch("enroll.wizard.collect", return_value=(self.vectors, {})), \
                     patch("enroll.store.ensure_dir", return_value=Path(tmp)), \
                     patch("enroll.store.save") as save, \
                     patch("enroll.confirm", return_value=consent):
                    code = enroll.main(["--confirm-stdin", "--quiet"])
                    self.assertEqual(code, 0 if consent else 1)
                    self.assertEqual(save.call_count, int(consent))

    def test_eof_declines_save(self):
        read, write = os.pipe()
        os.close(write)
        with os.fdopen(read) as stream:
            self.assertFalse(enroll.confirm(stream, timeout=0.1))


class DriftTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.owner = os.geteuid()
        store.save(self.path, "alice", rgb=vector(), metadata={}, owner=self.owner)
        self.original = (self.path / "alice.face").read_bytes()

    def update(self, score, accepted=True):
        return drift.update(self.path, "alice", {"accepted": accepted, "match_score": score}, self.owner)

    def test_third_low_success_suggests_without_changing_enrollment(self):
        self.assertFalse(self.update(0.54))
        self.assertFalse(self.update(0.4))
        self.assertTrue(self.update(0.5))
        self.assertEqual((self.path / "alice.face").read_bytes(), self.original)
        self.assertEqual((self.path / ".alice.drift.json").stat().st_mode & 0o777, 0o600)

    def test_failures_do_not_count_and_good_success_resets_streak(self):
        self.update(0.5)
        for _ in range(5):
            self.assertFalse(self.update(0.1, accepted=False))
        self.assertFalse(self.update(0.55))
        self.assertFalse(self.update(0.5))
        self.assertFalse(self.update(0.5))
        self.assertTrue(self.update(0.5))

    def test_new_enrollment_resets_prompt_and_counter(self):
        for _ in range(3):
            self.update(0.5)
        store.save(self.path, "alice", rgb=vector(1), metadata={}, owner=self.owner)
        self.assertFalse(self.update(0.5))

    def test_identical_reenrollment_also_clears_pending_suggestion(self):
        for _ in range(3):
            self.update(0.5)
        store.save(self.path, "alice", rgb=vector(), metadata={}, owner=self.owner)
        self.assertFalse((self.path / ".alice.drift.json").exists())
        self.assertFalse(self.update(0.5))

    def test_failed_reenrollment_preserves_pending_suggestion(self):
        for _ in range(3):
            self.update(0.5)
        with patch("facelock.store.os.replace", side_effect=OSError("disk error")), \
                self.assertRaises(OSError):
            store.save(self.path, "alice", rgb=vector(1), metadata={}, owner=self.owner)
        self.assertEqual((self.path / "alice.face").read_bytes(), self.original)
        self.assertTrue(self.update(0.5, accepted=False))

    def test_corrupt_state_and_invalid_scores_do_not_trigger_prompt(self):
        (self.path / ".alice.drift.json").write_text('[]')
        (self.path / ".alice.drift.json").chmod(0o600)
        self.assertFalse(self.update(float("nan")))
        self.assertFalse(self.update(0.5))

    def test_counter_is_bound_to_account(self):
        store.save(self.path, "bob", rgb=vector(), metadata={}, owner=self.owner)
        for _ in range(3):
            self.update(0.5)
        self.assertFalse(drift.update(self.path, "bob", {"accepted": True, "match_score": 0.5}, self.owner))


class FeedbackTests(unittest.TestCase):
    def event(self, state, attempt="a" * 32):
        return {"op": "publish", "state": state, "attempt": attempt, "reenroll_suggested": False}

    def test_only_root_can_publish_and_stale_attempts_cannot_finish_new_attempts(self):
        status = feedback.Status()
        self.assertFalse(status.accept(self.event("scanning"), 1000))
        self.assertTrue(status.accept(self.event("scanning"), 0))
        self.assertTrue(status.accept(self.event("scanning", "b" * 32), 0))
        self.assertFalse(status.accept(self.event("matched"), 0))
        self.assertEqual(status.current()["state"], "scanning")
        self.assertTrue(status.accept(self.event("matched", "b" * 32), 0))

    def test_crashed_worker_status_expires(self):
        clock = Mock(return_value=100)
        status = feedback.Status(clock)
        status.accept(self.event("scanning"), 0)
        clock.return_value = 146
        self.assertEqual(status.current()["state"], "idle")

    def test_missing_feedback_service_does_not_raise(self):
        with patch("facelock.feedback.endpoint", side_effect=FileNotFoundError):
            feedback.publish(os.geteuid(), self.event("scanning"))

    def test_no_face_is_distinct_from_failed_match(self):
        self.assertEqual(feedback.result_state({"accepted": True}), "matched")
        self.assertEqual(feedback.result_state({"sources": {"rgb": {"samples": [{"face": False}]}}}), "no-face")
        self.assertEqual(feedback.result_state({"sources": {"rgb": {"samples": [{"face": True}]}}}), "no-match")
        self.assertEqual(feedback.result_state({"reason": "camera-busy"}), "unavailable")

    def test_socket_protocol_fragmentation_watch_and_forged_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feedback.sock"
            stop = threading.Event()
            errors = []
            def run():
                try:
                    feedback.serve(path, stop)
                except Exception as exc:
                    errors.append(exc)
            thread = threading.Thread(target=run)
            thread.start()
            try:
                until = time.monotonic() + 2
                while not path.exists() and time.monotonic() < until:
                    time.sleep(0.01)
                with socket.socket(socket.AF_UNIX) as conn:
                    conn.settimeout(1)
                    conn.connect(str(path))
                    conn.sendall(b'{"op":')
                    conn.sendall(b'"watch"}\n')
                    with conn.makefile('rb') as stream:
                        self.assertEqual(json.loads(stream.readline())["state"], "idle")
                        with socket.socket(socket.AF_UNIX) as sender:
                            sender.connect(str(path))
                            sender.sendall(feedback.encode(self.event("scanning")))
                        if os.geteuid() == 0:
                            self.assertEqual(json.loads(stream.readline())["state"], "scanning")
                        else:
                            self.assertEqual(next(feedback.read_events(path))["state"], "idle")
                # Bad input is isolated to the client; the daemon still answers.
                with socket.socket(socket.AF_UNIX) as bad:
                    bad.connect(str(path))
                    bad.sendall(b'[1,2,3]\n')
                self.assertIn(next(feedback.read_events(path))["state"], ("idle", "scanning"))
            finally:
                stop.set()
                thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertFalse(errors, errors)
            self.assertFalse(path.exists())

    def test_symlink_runtime_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "link").symlink_to(p)
            import stat
            with self.assertRaises(PermissionError):
                feedback.private_path(p / "link", os.geteuid(), stat.S_ISDIR)


class UIProcessTests(unittest.TestCase):
    def test_no_save_message_before_ready_and_cancel_closes_pipe(self):
        ui = EnrollmentProcess(lambda _: None)
        ui.process = Mock(stdin=io.StringIO())
        ui.save()
        self.assertEqual(ui.process.stdin.getvalue(), "")
        ui.cancel()
        self.assertTrue(ui.process.stdin.closed)
        self.assertFalse(ui.ready)

    def test_save_requires_ready_and_writes_explicit_yes(self):
        ui = EnrollmentProcess(lambda _: None)
        ui.process = Mock()
        ui.ready = True
        ui.save()
        ui.process.stdin.write.assert_called_once_with("yes\n")
        ui.process.stdin.close.assert_called_once()
        self.assertFalse(ui.ready)

    def test_gui_cannot_choose_another_account_or_bypass_confirmation(self):
        from facelock.enrollment_ui import command
        with patch("facelock.enrollment_ui.Path.is_file", return_value=True):
            args = command()
        import pwd
        self.assertEqual(args[args.index("--user") + 1], pwd.getpwuid(os.getuid()).pw_name)
        self.assertIn("--confirm-stdin", args)
        self.assertNotIn("--yes", args)
