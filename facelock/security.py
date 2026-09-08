"""Filesystem trust checks for privileged entry points."""
import os
from pathlib import Path
import stat


def trusted_path(path, owner=0):
    """Reject writable/non-owned ancestors, including paths reached via symlinks."""
    path = Path(path).absolute()
    for candidate in (path, *path.parents, path.resolve(strict=True), *path.resolve().parents):
        info = candidate.lstat()
        if info.st_uid != owner and candidate != Path("/"):
            raise PermissionError(f"untrusted owner: {candidate}")
        if not stat.S_ISLNK(info.st_mode) and info.st_mode & 0o022:
            raise PermissionError(f"writable trusted path: {candidate}")
    return path.resolve(strict=True)


def private_read(path, owner=0, max_bytes=1_048_576):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner or
                info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > max_bytes):
            raise PermissionError(f"not a private regular file: {path}")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            return stream.read(max_bytes + 1)
    finally:
        os.close(fd)
