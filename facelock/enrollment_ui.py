"""Unprivileged enrollment process controller shared by the GTK window and tests."""
import json
from pathlib import Path
import pwd
import os
import subprocess
import threading


def command():
    library = Path(__file__).resolve().parents[1]
    launcher = library.parent.parent / "bin/facelock-run"
    if not launcher.is_file():
        raise FileNotFoundError("Install Facelock before opening the enrollment window.")
    user = pwd.getpwuid(os.getuid()).pw_name
    return ["/usr/bin/pkexec", str(launcher), str(library / "enroll.py"),
            "--user", user, "--progress-json", "--confirm-stdin"]


class EnrollmentProcess:
    def __init__(self, deliver, popen=subprocess.Popen):
        self.deliver = deliver
        self.popen = popen
        self.process = None
        self.cancelled = False
        self.ready = False

    def start(self):
        self.process = self.popen(command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, bufsize=1)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        errors = []
        def stderr():
            for line in self.process.stderr:
                errors.append(line.strip()[:300])
                del errors[:-8]
        error_reader = threading.Thread(target=stderr, daemon=True)
        error_reader.start()
        result = False
        for line in self.process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("event") == "ready":
                self.ready = True
            if event.get("event") == "result":
                result = True
            if not self.cancelled:
                self.deliver(event)
        code = self.process.wait()
        error_reader.join(timeout=1)
        if not result and not self.cancelled:
            self.deliver({"event": "result", "accepted": False,
                          "reason": "\n".join(errors[-3:]) or f"Enrollment exited ({code})."})
        self.deliver({"event": "finished"})

    def save(self):
        if not self.ready or self.cancelled:
            return
        self.ready = False
        try:
            self.process.stdin.write("yes\n")
            self.process.stdin.flush()
            self.process.stdin.close()
        except (OSError, ValueError):
            pass

    def cancel(self):
        self.cancelled = True
        self.ready = False
        if self.process is not None:
            # EOF declines saving. The privileged worker retains its deadline
            # and cleans up the camera; this UI never signals a root process.
            try:
                self.process.stdin.close()
            except (OSError, ValueError):
                pass
