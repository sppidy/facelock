"""Exercise real GTK widgets under Xvfb in CI; no camera or root helper runs."""
import importlib.util
import os
import unittest
from unittest.mock import patch

from facelock import gui


@unittest.skipUnless(os.environ.get("DISPLAY") and importlib.util.find_spec("gi"),
                     "GTK 4 and a display are needed (CI uses Xvfb)")
class GtkTests(unittest.TestCase):
    def test_enrollment_window_requires_save_and_handles_cancel(self):
        app = gui.create_application()
        app.register(None)
        app.activate()
        self.addCleanup(app.window.destroy)
        with patch("facelock.gui.EnrollmentProcess") as worker:
            worker.return_value.cancelled = False
            app.start(None)
            worker.return_value.start.assert_called_once()
            self.assertFalse(app.start_button.get_sensitive())
            self.assertFalse(app.save_button.get_sensitive())
            for count in (1, 2, 3):
                app.event({"event": "burst", "burst": count, "collected": count,
                           "usable": True, "sources": {}})
            self.assertEqual(app.bar.get_fraction(), 1)
            app.event({"event": "ready", "replacing": True})
            self.assertTrue(app.save_button.get_sensitive())
            self.assertIn("Replace", app.status.get_text())
            app.save(None)
            worker.return_value.save.assert_called_once()
            app.event({"event": "result", "accepted": True})
            self.assertIn("saved", app.status.get_text())
            app.event({"event": "finished"})
            self.assertTrue(app.start_button.get_sensitive())
            app.start(None)
            app.cancel(None)
            worker.return_value.cancel.assert_called_once()
            self.assertFalse(app.save_button.get_sensitive())


class GuiBoundaryTests(unittest.TestCase):
    def test_gui_refuses_to_run_as_root(self):
        with patch("facelock.gui.os.geteuid", return_value=0), \
             self.assertRaisesRegex(SystemExit, "normal desktop user"):
            gui.main()
