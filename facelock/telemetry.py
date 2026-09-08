"""Private attempt diagnostics: no frames, embeddings or environment values."""
import json
import os
import stat
import time

DEFAULT_LOG = "/var/lib/facelock/attempts.jsonl"


def emit(record, path=None):
    record = dict(record)
    record.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
    fd = None
    try:
        fd = os.open(path or DEFAULT_LOG,
                     os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW |
                     os.O_CLOEXEC | os.O_NONBLOCK, 0o600)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                info.st_mode & 0o077 or info.st_nlink != 1):
            return record
        os.write(fd, (json.dumps(record) + "\n").encode())
    except (OSError, ValueError, TypeError):
        pass
    finally:
        if fd is not None:
            os.close(fd)
    return record
