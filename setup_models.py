#!/usr/bin/env python3
"""Download YuNet + SFace ONNX models. Run with sudo (system model dir)."""
import os
import sys
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


def main():
    with open("/etc/facelock/config.yaml"
              if os.path.exists("/etc/facelock/config.yaml")
              else os.path.join(HERE, "..", "config.yaml")) as f:
        d = yaml.safe_load(f)["models"]["dir"]
    os.makedirs(d, exist_ok=True)
    for name, url in FILES.items():
        p = os.path.join(d, name)
        if (os.path.exists(p) and
                os.path.getsize(p) >= MIN_BYTES[name]):
            print(f"{name}: already present, skipping")
            continue
        print(f"downloading {name} ...", flush=True)
        urllib.request.urlretrieve(url, p + ".part")
        os.rename(p + ".part", p)
        if os.path.getsize(p) < MIN_BYTES[name]:
            sys.exit(f"{name}: download too small, failed?")
        print(f"{name}: ok ({os.path.getsize(p)} bytes)")


if __name__ == "__main__":
    main()
