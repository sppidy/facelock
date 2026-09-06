"""Root-owned 0600 embedding store."""
import os

import numpy as np


def _path(store_dir, user):
    return os.path.join(store_dir, f"{user}.npz")


def save(store_dir, user, rgb=None, ir=None):
    os.makedirs(store_dir, mode=0o700, exist_ok=True)
    data = {}
    if rgb is not None:
        data["rgb"] = np.asarray(rgb, dtype=np.float64)
    if ir is not None:
        data["ir"] = np.asarray(ir, dtype=np.float64)
    p = _path(store_dir, user)
    np.savez(p, **data)
    os.chmod(p, 0o600)


def load(store_dir, user):
    p = _path(store_dir, user)
    if not os.path.exists(p):
        return {}
    z = np.load(p)
    return {k: np.asarray(z[k], dtype=np.float64) for k in z.files}
