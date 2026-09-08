"""GTK enrollment window. Camera work runs in a separate privileged helper."""
import os
import pwd

from .enrollment_ui import EnrollmentProcess
from .wizard import describe


def create_application():
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gio, GLib, Gtk
    except (ImportError, ValueError):
        raise SystemExit("The enrollment window needs GTK 4 and Python GObject bindings.")

    class Application(Gtk.Application):
        def __init__(self):
            super().__init__(application_id="io.github.sppidy.Facelock",
                             flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
            self.worker = None
            self.window = None

        def do_activate(self):
            if self.window is not None:
                self.window.present()
                return
            self.window = Gtk.ApplicationWindow(application=self, title="Facelock enrollment")
            self.window.set_default_size(560, 450)
            self.window.connect("close-request", self.close_window)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            for side in ("top", "bottom", "start", "end"):
                getattr(box, "set_margin_" + side)(24)
            self.window.set_child(box)
            title = Gtk.Label(label=f"Set up face login for {pwd.getpwuid(os.getuid()).pw_name}", xalign=0)
            title.add_css_class("title-2")
            box.append(title)
            self.status = Gtk.Label(label="Look at the camera. We’ll collect three clear samples.",
                                    xalign=0, wrap=True)
            box.append(self.status)
            self.bar = Gtk.ProgressBar(show_text=True)
            self.bar.set_text("0 of 3 samples")
            box.append(self.bar)
            self.details = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                                        wrap_mode=Gtk.WrapMode.WORD_CHAR)
            scroll = Gtk.ScrolledWindow(vexpand=True, min_content_height=180)
            scroll.set_child(self.details)
            box.append(scroll)
            row = Gtk.Box(spacing=10)
            box.append(row)
            self.start_button = Gtk.Button(label="Start enrollment")
            self.save_button = Gtk.Button(label="Save enrollment", sensitive=False)
            self.cancel_button = Gtk.Button(label="Cancel", sensitive=False)
            for button, callback in ((self.start_button, self.start), (self.save_button, self.save),
                                     (self.cancel_button, self.cancel)):
                button.connect("clicked", callback)
                row.append(button)
            self.window.present()

        def start(self, _):
            self.bar.set_fraction(0)
            self.bar.set_text("0 of 3 samples")
            self.details.get_buffer().set_text("")
            self.status.set_text("Approve the system authorization prompt to open the camera.")
            self.worker = EnrollmentProcess(lambda event: GLib.idle_add(self.event, event))
            self.start_button.set_sensitive(False)
            self.cancel_button.set_sensitive(True)
            try:
                self.worker.start()
            except OSError as exc:
                self.status.set_text(str(exc))
                self.start_button.set_sensitive(True)
                self.cancel_button.set_sensitive(False)

        def event(self, event):
            kind = event.get("event")
            if self.worker.cancelled and kind != "finished":
                return False
            if kind in ("scanning", "burst"):
                count = event["collected"]
                self.bar.set_fraction(count / 3)
                self.bar.set_text(f"{count} of 3 samples")
                if kind == "scanning":
                    self.status.set_text(f"Look at the camera — capture {event['burst']}.")
                else:
                    buf = self.details.get_buffer()
                    buf.insert(buf.get_end_iter(), describe(event) + "\n\n")
            elif kind == "ready":
                verb = "Replace the existing enrollment" if event["replacing"] else "Save these samples"
                self.status.set_text(f"Three samples collected. {verb}? Confirm within 60 seconds.")
                self.save_button.set_sensitive(True)
            elif kind == "result":
                self.save_button.set_sensitive(False)
                self.status.set_text("Enrollment saved. You can close this window." if event["accepted"]
                                     else event.get("reason", "Enrollment could not be saved."))
            elif kind == "finished":
                self.start_button.set_sensitive(True)
                self.cancel_button.set_sensitive(False)
                self.save_button.set_sensitive(False)
            return False

        def save(self, _):
            self.save_button.set_sensitive(False)
            self.cancel_button.set_sensitive(False)
            self.status.set_text("Saving enrollment…")
            self.worker.save()

        def cancel(self, _):
            self.worker.cancel()
            self.save_button.set_sensitive(False)
            self.cancel_button.set_sensitive(False)
            self.status.set_text("Cancelled. Waiting for the camera to close; no samples will be saved.")

        def close_window(self, _):
            if self.worker is not None:
                self.worker.cancel()
            return False

    return Application()


def main():
    if os.geteuid() == 0:
        raise SystemExit("Run facelock-enroll as your normal desktop user.")
    create_application().run([])
