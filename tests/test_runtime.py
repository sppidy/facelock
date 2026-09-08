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
