#!/usr/bin/env python3
"""Download and verify YuNet + SFace ONNX models.

Run with sudo (system model dir). Model bytes are pinned so a partial,
corrupted, or unexpectedly replaced file is never silently accepted.
"""
import hashlib
import os
import stat
import sys
import tempfile
import urllib.error
import urllib.request

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
# NOTE: media.githubusercontent.com resolves Git-LFS pointers; raw.* does not.
BASE = ("https://media.githubusercontent.com/media/opencv/opencv_zoo"
        "/main/models")
FILES = {
    "face_detection_yunet_2023mar.onnx":
        f"{BASE}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx":
        f"{BASE}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
}
MIN_BYTES = {"face_detection_yunet_2023mar.onnx": 100_000,
             "face_recognition_sface_2021dec.onnx": 1_000_000}
SHA256 = {
    "face_detection_yunet_2023mar.onnx":
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    "face_recognition_sface_2021dec.onnx":
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
}


def _digest(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None
        digest = hashlib.sha256()
        with os.fdopen(fd, "rb", closefd=False) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return info.st_size, digest.hexdigest()
    except OSError:
        return None
    finally:
        os.close(fd)


def valid_model(path, name):
    result = _digest(path)
    return (result is not None and result[0] >= MIN_BYTES[name] and
            result[1] == SHA256[name])


def _download(url, path, name):
    fd, temporary = tempfile.mkstemp(prefix=f".{name}.", suffix=".part",
                                     dir=os.path.dirname(path))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as output, urllib.request.urlopen(url, timeout=120) as response:
            digest = hashlib.sha256()
            size = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        if size < MIN_BYTES[name] or digest.hexdigest() != SHA256[name]:
            raise RuntimeError(f"{name}: downloaded bytes failed SHA-256 verification")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    with open("/etc/facelock/config.yaml"
              if os.path.exists("/etc/facelock/config.yaml")
              else os.path.join(HERE, "..", "config.yaml")) as f:
        d = yaml.safe_load(f)["models"]["dir"]
    os.makedirs(d, exist_ok=True)
    for name, url in FILES.items():
        p = os.path.join(d, name)
        if valid_model(p, name):
            print(f"{name}: already present, skipping")
            continue
        print(f"downloading {name} with SHA-256 verification ...", flush=True)
        try:
            _download(url, p, name)
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            sys.exit(str(exc))
        print(f"{name}: ok ({os.path.getsize(p)} bytes)")


if __name__ == "__main__":
    main()
