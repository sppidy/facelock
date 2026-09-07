"""One-JSON-line-per-attempt telemetry for face-unlock (stdlib only)."""
import json
import os
import time

DEFAULT_LOG = "/var/lib/facelock/attempts.jsonl"


def emit(record, path=None):
    """Append record as one JSON line. Never raises (auth must not hang)."""
    path = path or os.environ.get("FACELOCK_LOG", DEFAULT_LOG)
    try:
        record = dict(record)
        record.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
        with open(path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass
    return record
