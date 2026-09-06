"""Embedding store.

Layout is group-readable by `video` (not root-only): sudo runs PAM auth
helpers as the *invoking* user, so face-unlock must read embeddings and
append logs without root. The video group on this box is just the owner.
"""
import grp
import os

import numpy as np

STORE_MODE_DIR = 0o750
STORE_MODE_FILE = 0o640
STORE_GROUP = "video"


def _group_to(path):
    try:
        gid = grp.getgrnam(STORE_GROUP).gr_gid
        os.chown(path, -1, gid)
    except (KeyError, PermissionError, OSError):
        pass


def _path(store_dir, user):
    return os.path.join(store_dir, f"{user}.npz")


def ensure_dir(store_dir):
    os.makedirs(store_dir, mode=STORE_MODE_DIR, exist_ok=True)
    try:
        os.chmod(store_dir, STORE_MODE_DIR)
    except OSError:
        pass
    _group_to(store_dir)


def save(store_dir, user, rgb=None, ir=None):
    ensure_dir(store_dir)
    data = {}
    if rgb is not None:
        data["rgb"] = np.asarray(rgb, dtype=np.float64)
    if ir is not None:
        data["ir"] = np.asarray(ir, dtype=np.float64)
    p = _path(store_dir, user)
    np.savez(p, **data)
    try:
        os.chmod(p, STORE_MODE_FILE)
    except OSError:
        pass
    _group_to(p)


def load(store_dir, user):
    p = _path(store_dir, user)
    if not os.path.exists(p):
        return {}
    z = np.load(p)
    return {k: np.asarray(z[k], dtype=np.float64) for k in z.files}
