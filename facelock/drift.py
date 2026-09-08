"""Re-enrollment suggestions; never change identity records or match thresholds."""
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

from . import security, store

THRESHOLD = 0.55
COUNT = 3


def update(directory, user, detail, owner=0):
    """Call under the capture lock. Count consecutive low accepted matches."""
    store.valid_user(user)
    directory = store.ensure_dir(directory, owner)
    identity = hashlib.sha256(security.private_read(directory / f"{user}.face", owner)).hexdigest()
    path = directory / f".{user}.drift.json"
    state = {"identity": identity, "count": 0, "suggested": False}
    try:
        saved = json.loads(security.private_read(path, owner, 4096))
        if (saved.get("identity") == identity and type(saved.get("count")) is int
                and 0 <= saved["count"] <= COUNT and type(saved.get("suggested")) is bool):
            state = saved
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    if not detail.get("accepted"):
        return state["suggested"]
    score = detail.get("match_score")
    if type(score) not in (int, float) or not math.isfinite(score):
        return state["suggested"]
    state["count"] = min(COUNT, state["count"] + 1) if score < THRESHOLD else 0
    state["suggested"] = state["suggested"] or state["count"] == COUNT
    fd, temporary = tempfile.mkstemp(prefix=".drift-", dir=directory)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return state["suggested"]
