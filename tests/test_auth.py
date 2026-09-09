import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from facelock import auth, config, liveness, store, recognize, telemetry
from facelock.acquisition import Burst
from facelock.security import private_read
import runner


def settings(required=("rgb", "ir")):
    cfg = config.merge(config.DEFAULTS, {"cameras": {"rgb": "rgb-camera", "ir": "ir-camera"},
                                       "auth": {"required_sensors": list(required)}})
    return config.validate(cfg)


def vector(index=0):
    v = np.zeros(128)
    v[index] = 1
    return v


def burst():
    p = liveness.challenge_pattern("test", 8)
    frames = [np.full((32, 32), 120 if lit else 8, np.uint8) for lit in p]
    texture = (np.indices((32, 32)).sum(axis=0) % 2 * 60 + 70).astype(np.uint8)
    img = np.repeat(texture[:, :, None], 3, axis=2)
    return Burst({s: [img.copy() for _ in range(3)] for s in ("rgb", "ir")}, frames, p, {})


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.cfg = settings()
        self.refs = {s: vector() for s in ("rgb", "ir")}
        self.models = lambda _: (None, None)
        self.embed = patch("facelock.recognize.embed",
                           return_value=(vector(), 0.9, (0, 0, 32, 32), None))
        self.embed.start()
        self.addCleanup(self.embed.stop)

    def verify(self, frames=None, refs=None, metadata=None):
        return auth.verify(self.cfg, self.refs if refs is None else refs,
                           auth.enrollment_metadata(self.cfg) if metadata is None else metadata,
                           models_fn=self.models, capture_fn=lambda _: frames or burst())

    def test_both_domains_and_illumination_pass(self):
        ok, detail = self.verify()
        self.assertTrue(ok, detail)
        self.assertEqual(detail["fusion"]["required"], ["rgb", "ir"])

    def test_drift_score_uses_unrounded_second_best_required_match(self):
        with patch("facelock.recognize.similarity", side_effect=[0.95, 0.54999, 0.2, 0.9, 0.8, 0.75]):
            ok, detail = self.verify()
        self.assertTrue(ok)
        self.assertEqual(detail["match_score"], 0.54999)

    def test_missing_enrollment_never_opens_camera(self):
        from unittest.mock import Mock
        capture = Mock(side_effect=AssertionError("must not capture"))
        ok, _ = auth.verify(self.cfg, {"ir": vector()}, auth.enrollment_metadata(self.cfg),
                            models_fn=self.models, capture_fn=capture)
        self.assertFalse(ok)
        capture.assert_not_called()

    def test_single_domain_mismatch_rejects(self):
        self.assertFalse(self.verify(refs={"rgb": vector(), "ir": vector(1)})[0])

    def test_no_face_one_sensor_rejects(self):
        b = burst()
        b.images["rgb"] = []
        self.assertFalse(self.verify(b)[0])

    def test_one_good_frame_is_insufficient(self):
        b = burst()
        b.images["rgb"] = b.images["rgb"][:1]
        self.assertFalse(self.verify(b)[0])

    def test_missing_and_flat_challenge_reject(self):
        for ir_frames, flags in (([], []), ([np.full((32, 32), 70)] * 8, [0, 1] * 4)):
            b = burst()
            b.ir_frames, b.pattern = ir_frames, flags
            self.assertFalse(self.verify(b)[0])

    def test_capture_errors_propagate_for_cli_failure(self):
        def broken(_):
            raise RuntimeError("camera busy")
        with self.assertRaisesRegex(RuntimeError, "camera busy"):
            auth.verify(self.cfg, self.refs, auth.enrollment_metadata(self.cfg),
                        models_fn=self.models, capture_fn=broken)

    def test_enrollment_capture_settings_must_match(self):
        old = auth.enrollment_metadata(self.cfg)
        self.cfg["ir_led"]["mode"] = "torch"
        self.assertFalse(self.verify(metadata=old)[0])

    def test_capability_check_does_not_invalidate_enrollment(self):
        self.cfg["runtime"].update(stack="/camera", tuning="/tuning",
                                   require_camss=False)
        old = auth.enrollment_metadata(self.cfg)
        self.cfg["runtime"]["require_camss"] = True
        self.assertEqual(auth.enrollment_metadata(self.cfg), old)
        self.cfg["runtime"]["stack"] = "/different-camera"
        self.assertNotEqual(auth.enrollment_metadata(self.cfg), old)

    def test_optional_attention_rejects_away_rgb_samples_without_reenrollment(self):
        old = auth.enrollment_metadata(self.cfg)
        self.cfg["attention"]["enabled"] = True
        self.assertEqual(auth.enrollment_metadata(self.cfg), old)
        away = {"required": True, "ok": False, "reason": "eyes-looking-away"}
        rejected = (None, 0.9, (0, 0, 32, 32), away)
        accepted = (vector(), 0.9, (0, 0, 32, 32), None)
        with patch("facelock.recognize.embed",
                   side_effect=[rejected] * 3 + [accepted] * 3):
            ok, detail = self.verify()
        self.assertFalse(ok)
        self.assertTrue(all(sample["reason"] == "eyes-looking-away"
                            for sample in detail["sources"]["rgb"]["samples"]))

    def test_enroll_and_verify_share_pipeline(self):
        refs, detail = auth.enroll(self.cfg, models_fn=self.models, capture_fn=lambda _: burst())
        self.assertEqual(set(refs), {"rgb", "ir"})
        self.assertTrue(self.verify(refs=refs)[0])
        b = burst()
        b.images["rgb"] = []
        self.assertIsNone(auth.enroll(self.cfg, models_fn=self.models, capture_fn=lambda _: b)[0])

    def test_ir_only_requires_explicit_policy_and_fresh_metadata(self):
        old = auth.enrollment_metadata(self.cfg)
        self.cfg["auth"]["required_sensors"] = ["ir"]
        self.assertFalse(self.verify(refs={"ir": vector()}, metadata=old)[0])
        self.assertTrue(self.verify(refs={"ir": vector()})[0])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.owner = os.geteuid()
        self.save()

    def save(self, user="alice"):
        store.save(self.path, user, rgb=vector(), metadata={"version": "test"}, owner=self.owner)

    def load(self, user="alice"):
        return store.load(self.path, user, owner=self.owner)

    def test_sealed_roundtrip_and_modes(self):
        refs, md = self.load()
        np.testing.assert_allclose(refs["rgb"], vector())
        self.assertEqual(md, {"version": "test"})
        for name in ("alice.face", ".seal-key"):
            self.assertEqual((self.path / name).stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(self.path.glob(".enroll-*")))

    def test_payload_tamper_rejects_before_numpy(self):
        path = self.path / "alice.face"
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(data)
        with patch("facelock.store.np.load", side_effect=AssertionError("must not parse")):
            with self.assertRaisesRegex(ValueError, "integrity"):
                self.load()

    def test_user_binding_prevents_copying_enrollment(self):
        shutil.copyfile(self.path / "alice.face", self.path / "bob.face")
        (self.path / "bob.face").chmod(0o600)
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.load("bob")

    def test_symlinks_and_readable_files_reject(self):
        p = self.path / "alice.face"
        p.chmod(0o640)
        with self.assertRaises(PermissionError):
            self.load()
        p.chmod(0o600)
        (self.path / "bob.face").symlink_to(p)
        with self.assertRaises(OSError):
            self.load("bob")

    def test_group_writable_directory_rejects(self):
        self.path.chmod(0o770)
        with self.assertRaises(PermissionError):
            self.load()
        self.path.chmod(0o700)

    def test_fifo_rejects_without_blocking(self):
        path = self.path / "fifo.face"
        os.mkfifo(path, mode=0o600)
        with self.assertRaises(PermissionError):
            private_read(path, self.owner)

    def test_legacy_is_not_imported(self):
        np.savez(self.path / "bob.npz", rgb=vector())
        self.assertEqual(self.load("bob"), ({}, {}))

    def test_invalid_vectors_and_accounts(self):
        for v in (np.zeros(128), np.ones(4), np.full(128, np.nan)):
            with self.assertRaises(ValueError):
                store.save(self.path, "alice", rgb=v, metadata={}, owner=self.owner)
        for user in ("../root", "", "bob/alice", "a\nb"):
            with self.assertRaises(ValueError):
                self.load(user)


class PolicyTests(unittest.TestCase):
    def test_invalid_configuration(self):
        changes = [{"auth": {"required_sensors": []}},
                   {"auth": {"required_sensors": ["ir", "ir"]}},
                   {"auth": {"required_sensors": [False]}},
                   {"dual": {"buffers": 3.5}},
                   {"challenge": {"phases": 7}},
                   {"challenge": {"discard_frames": 0}},
                   {"capture": {"timeout_sec": float("nan")}},
                   {"match": {"ir_threshold": -0.5}},
                   {"attention": {"enabled": "yes"}},
                   {"attention": {"min_head_pitch": 0.8, "max_head_pitch": 0.7}},
                   {"quality": {"brightness_range": [200, 10]}}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises((ValueError, TypeError)):
                config.validate(config.merge(settings(), change))

    def test_attention_requires_rgb_when_enabled(self):
        cfg = settings(("ir",))
        with self.assertRaisesRegex(ValueError, "requires rgb"):
            config.validate(config.merge(cfg, {"attention": {"enabled": True}}))

    def test_all_shipped_profiles_load(self):
        root = Path(__file__).resolve().parents[1]
        for path in [root / "config.yaml", *root.glob("profiles/*.yaml")]:
            with self.subTest(path=path):
                config.load(path)

    def test_runtime_ignores_inherited_loader_settings(self):
        with patch.dict(os.environ, {"PYTHONPATH": "/tmp/evil", "LD_PRELOAD": "/tmp/evil.so",
                                     "FACELOCK_STAGED": "/tmp/evil", "FACELOCK_LOG": "/tmp/evil"}):
            env, path = runner.runtime_environment(settings(), privileged=True)
        for variable in ("PYTHONPATH", "LD_PRELOAD", "FACELOCK_STAGED", "FACELOCK_LOG"):
            self.assertNotIn(variable, env)
        self.assertIsNone(path)

    def test_telemetry_does_not_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "target"
            target.write_text("untouched")
            link = Path(temp) / "log"
            link.symlink_to(target)
            telemetry.emit({"event": "test"}, link)
            self.assertEqual(target.read_text(), "untouched")


class RunnerTests(unittest.TestCase):
    def test_worker_can_be_stopped_after_timeout(self):
        import subprocess
        import sys
        import time
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                 start_new_session=True)
        start = time.monotonic()
        runner.stop_worker(child)
        self.assertIsNotNone(child.poll())
        self.assertLess(time.monotonic() - start, 3)

    def test_pam_account_cannot_be_substituted(self):
        from facelock import cli
        cfg = settings()
        with patch.dict(os.environ, {"PAM_USER": "bob", "SUDO_USER": "alice"}), \
             patch("facelock.cli.pwd.getpwnam"):
            self.assertEqual(cli.account(None, cfg, pam=True), "bob")
            with self.assertRaises(PermissionError):
                cli.account("alice", cfg, pam=True)
