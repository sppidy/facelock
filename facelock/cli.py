"""Shared privileged CLI boundary and attempt lifecycle."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import pwd
import signal
import stat

from . import config, security, store

CONFIG = "/etc/facelock/config.yaml"


def arguments(ap):
    ap.add_argument("--config", default=CONFIG)
    ap.add_argument("--user")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--json", action="store_true")


def load_config(path):
    if os.geteuid() != 0:
        raise PermissionError("enrollment and verification require root; use sudo facelock-run")
    security.trusted_path(path)
    cfg = config.load(path)
    security.trusted_path(Path(cfg["store"]["dir"]).parent)
    security.trusted_path(cfg["models"]["dir"])
    for name in ("face_detection_yunet_2023mar.onnx", "face_recognition_sface_2021dec.onnx"):
        security.trusted_path(Path(cfg["models"]["dir"]) / name)
    return cfg


def account(requested, cfg, pam=False):
    if pam:
        user = os.environ.get("PAM_USER", "")
        if requested and requested != user:
            raise PermissionError("PAM account mismatch")
    else:
        user = requested or os.environ.get("SUDO_USER") or cfg["pam"]["user"]
    store.valid_user(user)
    pwd.getpwnam(user)
    if cfg["pam"]["user"] and user != cfg["pam"]["user"]:
        raise PermissionError("account is not enabled in pam.user")
    return user


def _terminated(signum, frame):
    raise TimeoutError("authentication attempt terminated")


@contextmanager
def attempt(cfg):
    directory = store.ensure_dir(cfg["store"]["dir"])
    fd = os.open(directory / ".capture.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    handlers = {}
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise PermissionError("invalid capture lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another facelock attempt is in progress") from None
        for sig in (signal.SIGTERM, signal.SIGINT):
            handlers[sig] = signal.signal(sig, _terminated)
        yield
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        os.close(fd)


def report(detail, as_json=False):
    if as_json:
        print(json.dumps(detail, sort_keys=True))
        return
    for source, result in detail.get("sources", {}).items():
        scores = [s["score"] for s in result.get("samples", [])]
        print(f"{source}: face_scores={scores} similarities={result.get('similarities', [])} "
              f"usable={result.get('usable', 0)} match={result.get('match', False)}")
    illumination = detail.get("illumination")
    if illumination:
        print(f"challenge: {illumination}")
    print("accepted=" + str(detail.get("accepted", False)) +
          (" reason=" + detail["reason"] if detail.get("reason") else ""))
    if detail.get("enrolled"):
        print(f"enrolled {detail['enrolled']}: {', '.join(detail['sensors'])}")
