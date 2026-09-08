"""Atomic root-only enrollment with HMAC integrity (not encryption).

Legacy unsealed .npz files are never used for authentication. Re-enrollment
is required rather than blessing files from a formerly writable directory.
"""
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import stat
import tempfile

import numpy as np

from .security import private_read

MAGIC = b"FACELOCK2\n"
STORE_MODE_DIR = 0o700
STORE_MODE_FILE = 0o600


def valid_user(user):
    if not isinstance(user, str) or not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,63}\$?", user):
        raise ValueError("invalid account name")
    return user


def ensure_dir(store_dir, owner=0):
    path = Path(store_dir)
    path.mkdir(mode=STORE_MODE_DIR, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or info.st_mode & 0o077:
        raise PermissionError(f"store must be {owner}-owned mode 0700: {path}")
    return path


def _key(directory, create, owner):
    path = directory / ".seal-key"
    if create:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as f:
                f.write(os.urandom(32))
                f.flush()
                os.fsync(f.fileno())
    key = private_read(path, owner, 32)
    if len(key) != 32:
        raise ValueError("invalid store seal key")
    return key


def _vectors(data):
    if not data or set(data) - {"rgb", "ir"}:
        raise ValueError("invalid enrollment domains")
    result = {}
    for sensor, values in data.items():
        a = np.asarray(values, dtype=np.float64)
        norm = np.linalg.norm(a)
        if a.shape != (128,) or not np.isfinite(a).all() or not 0.99 <= norm <= 1.01:
            raise ValueError(f"invalid {sensor} embedding")
        result[sensor] = a / norm
    return result


def save(store_dir, user, *, metadata, owner=0, **vectors):
    valid_user(user)
    directory = ensure_dir(store_dir, owner)
    key = _key(directory, True, owner)
    output = io.BytesIO()
    np.savez(output, **_vectors(vectors),
             _metadata=np.frombuffer(json.dumps(metadata, sort_keys=True).encode(), np.uint8))
    payload = output.getvalue()
    mac = hmac.digest(key, user.encode() + b"\0" + payload, "sha256")
    fd, temporary = tempfile.mkstemp(prefix=".enroll-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), STORE_MODE_FILE)
            f.write(MAGIC + mac + payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, directory / f"{user}.face")
        # Explicit re-enrollment clears suggestions even if the saved vectors
        # are identical. Remove state only after replacement succeeds.
        (directory / f".{user}.drift.json").unlink(missing_ok=True)
        dfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load(store_dir, user, *, owner=0):
    valid_user(user)
    directory = ensure_dir(store_dir, owner)
    try:
        blob = private_read(directory / f"{user}.face", owner)
    except FileNotFoundError:
        return {}, {}
    if not blob.startswith(MAGIC) or len(blob) <= len(MAGIC) + 32:
        raise ValueError("invalid sealed enrollment")
    key = _key(directory, False, owner)
    mac, payload = blob[len(MAGIC):len(MAGIC) + 32], blob[len(MAGIC) + 32:]
    expected = hmac.digest(key, user.encode() + b"\0" + payload, "sha256")
    if not hmac.compare_digest(mac, expected):
        raise ValueError("enrollment integrity check failed")
    # Do not parse arrays until the complete payload and account binding verify.
    with np.load(io.BytesIO(payload), allow_pickle=False) as z:
        metadata = json.loads(z["_metadata"].tobytes())
        vectors = _vectors({k: z[k] for k in z.files if k != "_metadata"})
    return vectors, metadata
