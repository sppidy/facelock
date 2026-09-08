"""Optional GTK status binding. Never initiates or accepts authentication."""
import os
import threading

from . import feedback


class StatusLabel:
    def __init__(self, label, glib):
        self.label, self.glib = label, glib
        self.stopped = threading.Event()
        label.connect('destroy', lambda *_: self.stopped.set())
        threading.Thread(target=self._poll, daemon=True).start()

    def _show(self, value):
        if not self.stopped.is_set():
            self.label.set_text(value)
        return False

    def _poll(self):
        previous = None
        while not self.stopped.is_set():
            try:
                event = next(feedback.read_events(feedback.endpoint(os.getuid())))
                value = feedback.label(event)
            except (OSError, ValueError, TypeError, AttributeError, StopIteration):
                value = ''
            if value != previous:
                self.glib.idle_add(self._show, value)
                previous = value
            self.stopped.wait(.5)
