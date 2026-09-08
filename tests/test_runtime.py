import os
from pathlib import Path
import signal
import sysconfig
import tempfile
import unittest
from unittest.mock import Mock, patch

import runner
from tools.stage_camera_runtime import stage


class RuntimeTests(unittest.TestCase):
    def test_snapshot_includes_executable_proxy_and_dereferences_binding_files(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / "build-tree", Path(temp) / "snapshot"
            binding = "build/src/py/libcamera/_libcamera" + sysconfig.get_config_var("EXT_SUFFIX")
            worker = "build/src/libcamera/proxy/worker/soft_ipa_proxy"
            for name in ("build/src/libcamera/libcamera.so.0.7.1",
                         "build/src/libcamera/base/libcamera-base.so.0.7.1",
                         "build/src/ipa/simple/ipa_soft_simple.so", binding, worker,
                         "opt-in/libcamera/configuration.yaml", "source/init.py"):
                p = source / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"fixture")
            (source / "build/src/py/libcamera/__init__.py").symlink_to(source / "source/init.py")
            stage(source, target)
            self.assertTrue(os.access(target / worker, os.X_OK))
            self.assertEqual((target / binding).read_bytes(), b"fixture")
            self.assertFalse((target / "build/src/py/libcamera/__init__.py").is_symlink())
            with self.assertRaises(FileExistsError):
                stage(source, target)
            (source / worker).unlink()
            with self.assertRaises(FileNotFoundError):
                stage(source, Path(temp) / "incomplete")

    def test_worker_exit_between_poll_and_signal_still_gets_reaped(self):
        worker = Mock(pid=123)
        worker.poll.return_value = None
        with patch("runner.os.killpg", side_effect=ProcessLookupError) as kill:
            runner.stop_worker(worker)
        kill.assert_called_once_with(123, signal.SIGTERM)
        worker.wait.assert_called_once_with()


class CamssPrerequisiteTests(unittest.TestCase):
    def test_running_driver_capability_is_required(self):
        from facelock.kernel import require_camss
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(RuntimeError, 'CAMSS'):
                require_camss(root)
            device = root / 'acb7000.isp'
            device.mkdir()
            marker = device / 'facelock_capability'
            marker.write_text('unsupported\n')
            with self.assertRaises(RuntimeError):
                require_camss(root)
            marker.write_text('x1p-normal-world-v1\n')
            require_camss(root)

    def test_a14_cannot_disable_mandatory_kernel_requirement(self):
        from facelock import config
        cfg = config.load(Path(__file__).resolve().parents[1] / 'profiles/zenbook-a14.yaml')
        self.assertEqual(cfg['runtime']['stack'], '/usr/lib/facelock/camera')
        self.assertTrue(cfg['runtime']['require_camss'])
        cfg['runtime']['require_camss'] = False
        with self.assertRaisesRegex(ValueError, 'CAMSS|camss'):
            config.validate(cfg)

    def test_missing_kernel_capability_prevents_runtime_launch(self):
        from test_auth import settings
        cfg = settings()
        cfg['runtime']['require_camss'] = True
        with patch('facelock.kernel.require_camss', side_effect=RuntimeError('missing CAMSS')):
            with self.assertRaisesRegex(RuntimeError, 'missing CAMSS'):
                runner.runtime_environment(cfg, False)

    def test_direct_capture_also_requires_kernel_capability(self):
        from facelock import acquisition
        from test_auth import settings
        cfg = settings()
        cfg['runtime']['require_camss'] = True
        session = Mock()
        with patch('facelock.kernel.require_camss', side_effect=RuntimeError('missing CAMSS')):
            with self.assertRaisesRegex(RuntimeError, 'missing CAMSS'):
                acquisition.capture(cfg, session_factory=session)
        session.assert_not_called()

    def test_python_abi_mismatch_rejects_before_loading_binding(self):
        import json
        from test_auth import settings
        cfg = settings()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('build/src/libcamera', 'build/src/ipa/simple',
                         'build/src/py/libcamera', 'build/src/libcamera/proxy/worker',
                         'opt-in/libcamera'):
                (root / name).mkdir(parents=True, exist_ok=True)
            for name in ('build/src/py/libcamera/__init__.py',
                         'build/src/libcamera/proxy/worker/soft_ipa_proxy',
                         'opt-in/libcamera/configuration.yaml'):
                (root / name).write_text('')
            (root / 'manifest.json').write_text(json.dumps({'python_abi': 'wrong-python'}))
            cfg['runtime']['stack'] = str(root)
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ValueError, 'Python ABI'):
                    runner.runtime_environment(cfg, False)
