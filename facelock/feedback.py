"""Local UI status transport. Messages never authorize an unlock."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import pwd
import selectors
import socket
import stat
import struct
import time
import uuid

STATES = {"scanning", "matched", "no-face", "no-match", "unavailable"}
LABELS = {"idle": "", "scanning": "Looking for your face…", "matched": "Face matched",
          "no-face": "No face detected", "no-match": "Face not matched — use your password",
          "unavailable": "Face unlock unavailable — use your password"}
LIMIT = 4096


def peer_uid(conn):
    return struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]


def private_path(path, uid, kind):
    info = path.lstat()
    if info.st_uid != uid or info.st_mode & 0o077 or not kind(info.st_mode):
        raise PermissionError(f"not a private feedback path: {path}")


def endpoint(uid, create=False):
    runtime = Path(f"/run/user/{uid}")
    private_path(runtime, uid, stat.S_ISDIR)
    directory = runtime / "facelock"
    if create:
        directory.mkdir(mode=0o700, exist_ok=True)
    private_path(directory, uid, stat.S_ISDIR)
    return directory / "feedback.sock"


def encode(message):
    return (json.dumps(message, separators=(",", ":")) + "\n").encode()


def publish(uid, event):
    """A missing or slow UI must not delay authentication."""
    try:
        path = endpoint(uid)
        private_path(path, uid, stat.S_ISSOCK)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(0.05)
            conn.connect(str(path))
            if peer_uid(conn) != uid:
                return
            conn.sendall(encode({"op": "publish", **event}))
    except (OSError, ValueError):
        pass


class Reporter:
    def __init__(self, user, cfg):
        self.uid = pwd.getpwnam(user).pw_uid
        self.greeter = cfg["feedback"]["greeter_user"] if os.environ.get("PAM_SERVICE") == "greetd" else ""
        self.attempt = uuid.uuid4().hex

    def send(self, state, suggested=False):
        event = {"state": state, "attempt": self.attempt,
                 "reenroll_suggested": bool(suggested)}
        publish(self.uid, event)
        if self.greeter:
            try:
                uid = pwd.getpwnam(self.greeter).pw_uid
                if uid != self.uid:
                    # A greeter gets transient status, never a user's drift history.
                    publish(uid, {**event, "reenroll_suggested": False})
            except KeyError:
                pass


def result_state(detail):
    if detail.get("accepted"):
        return "matched"
    sources = detail.get("sources", {})
    if not sources:
        return "unavailable"
    if not any(sample.get("face") for source in sources.values() for sample in source["samples"]):
        return "no-face"
    return "no-match"


class Status:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.event = {"state": "idle", "reenroll_suggested": False}
        self.expires = 0
        self.attempt = None

    def accept(self, message, uid):
        if uid != 0 or message.get("state") not in STATES:
            return False
        attempt = message.get("attempt")
        if not isinstance(attempt, str) or len(attempt) != 32:
            return False
        if message["state"] != "scanning" and attempt != self.attempt:
            return False
        if type(message.get("reenroll_suggested")) is not bool:
            return False
        self.attempt = attempt
        self.event = {"state": message["state"],
                      "reenroll_suggested": message["reenroll_suggested"]}
        self.expires = self.clock() + (45 if message["state"] == "scanning" else 5)
        return True

    def current(self):
        if self.clock() >= self.expires:
            return {"state": "idle", "reenroll_suggested": self.event["reenroll_suggested"]}
        return dict(self.event)


def serve(path, stop=None):
    """Bounded nonblocking clients; only root peers may publish status."""
    uid = os.geteuid()
    state = Status()
    selector = selectors.DefaultSelector()
    clients = {}
    lockfd = os.open(path.parent / "feedback.lock",
                     os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    bound = False
    def close(conn):
        selector.unregister(conn)
        clients.pop(conn, None)
        conn.close()
    def queue(conn, value):
        data = clients[conn]
        data["out"] += encode(value)
        if len(data["out"]) > LIMIT:
            close(conn)
        else:
            selector.modify(conn, selectors.EVENT_READ | selectors.EVENT_WRITE)
    try:
        info = os.fstat(lockfd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise PermissionError("invalid feedback lock")
        fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if path.exists() or path.is_symlink():
            private_path(path, uid, stat.S_ISSOCK)
            path.unlink()
        listener.bind(str(path))
        bound = True
        path.chmod(0o600)
        listener.listen(16)
        listener.setblocking(False)
        selector.register(listener, selectors.EVENT_READ)
        previous = state.current()
        while stop is None or not stop.is_set():
            for key, mask in selector.select(0.1):
                conn = key.fileobj
                if conn is listener:
                    conn, _ = listener.accept()
                    if len(clients) >= 32 or peer_uid(conn) not in (0, uid):
                        conn.close()
                        continue
                    conn.setblocking(False)
                    clients[conn] = {"in": b"", "out": b"", "watch": False,
                                     "done": False, "since": time.monotonic()}
                    selector.register(conn, selectors.EVENT_READ)
                    continue
                data = clients[conn]
                try:
                    if mask & selectors.EVENT_READ:
                        chunk = conn.recv(LIMIT)
                        if not chunk or data["done"]:
                            close(conn)
                            continue
                        data["in"] += chunk
                        if len(data["in"]) > LIMIT:
                            close(conn)
                            continue
                        if b"\n" in data["in"]:
                            message = json.loads(data["in"].split(b"\n", 1)[0])
                            if not isinstance(message, dict):
                                close(conn)
                                continue
                            op = message.get("op")
                            if op == "publish":
                                state.accept(message, peer_uid(conn))
                                close(conn)
                                continue
                            if op not in ("get", "watch"):
                                close(conn)
                                continue
                            data["done"], data["watch"] = True, op == "watch"
                            queue(conn, state.current())
                    if conn in clients and mask & selectors.EVENT_WRITE:
                        count = conn.send(data["out"])
                        data["out"] = data["out"][count:]
                        if not data["out"]:
                            if not data["watch"]:
                                close(conn)
                            else:
                                selector.modify(conn, selectors.EVENT_READ)
                except (OSError, ValueError, TypeError):
                    if conn in clients:
                        close(conn)
            current = state.current()
            if current != previous:
                for conn, data in list(clients.items()):
                    if data["watch"]:
                        queue(conn, current)
                previous = current
            for conn, data in list(clients.items()):
                if not data["watch"] and time.monotonic() - data["since"] > 1:
                    close(conn)
    finally:
        for conn in list(clients):
            close(conn)
        listener.close()
        selector.close()
        if bound:
            path.unlink(missing_ok=True)
        os.close(lockfd)


def read_events(path, watch=False):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(1)
        conn.connect(str(path))
        if peer_uid(conn) != os.geteuid():
            raise PermissionError("unexpected feedback service owner")
        conn.sendall(encode({"op": "watch" if watch else "get"}))
        if watch:
            conn.settimeout(None)
        with conn.makefile("rb") as stream:
            while line := stream.readline(LIMIT + 1):
                if len(line) > LIMIT:
                    raise ValueError("oversized feedback message")
                event = json.loads(line)
                if event.get("state") not in LABELS:
                    raise ValueError("unknown feedback state")
                yield event
                if not watch:
                    return


def label(event):
    if event.get("reenroll_suggested") and event["state"] in ("matched", "idle"):
        return "Face matched less clearly lately. Consider enrolling again."
    return LABELS[event["state"]]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    modes = ap.add_mutually_exclusive_group()
    modes.add_argument("--serve", action="store_true")
    modes.add_argument("--watch", action="store_true")
    ap.add_argument("--text", action="store_true", help="print labels instead of JSON")
    args = ap.parse_args(argv)
    try:
        path = endpoint(os.geteuid(), create=args.serve)
        if args.serve:
            serve(path)
        else:
            for event in read_events(path, args.watch):
                print(label(event) if args.text else json.dumps(event), flush=True)
    except (OSError, ValueError) as exc:
        if args.serve:
            ap.exit(1, f"facelock-feedback: {exc}\n")
        # A missing UI service leaves a lock-screen label empty.
        print("" if args.text else json.dumps({"state": "idle", "reenroll_suggested": False}))
    except (KeyboardInterrupt, BrokenPipeError):
        pass
