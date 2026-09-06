#!/usr/bin/env python3
"""IR diagnostic: capture with flash, detect face, save preview.
Sit upright facing the laptop (normal login posture), then run:
    python ir_check.py
"""
import os
import sys

import cv2
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from facelock import ir_capture, recognize

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    with open("/etc/facelock/config.yaml"
              if os.path.exists("/etc/facelock/config.yaml")
              else os.path.join(HERE, "config.yaml")) as f:
        cfg = yaml.safe_load(f)
    img = ir_capture.snapshot_ir(
        cfg["cameras"]["ir"], cfg["ir_led"]["path"],
        cfg["ir_led"]["brightness"], cfg["ir_capture"]["frames"])
    det, rec = recognize.load_models(cfg["models"]["dir"])
    v, s = recognize.embed(img, det, rec, cfg["match"]["detector_min_score"])
    print(f"face_score={s:.2f} {'FACE OK' if v is not None else 'NO FACE'}")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    boost = cv2.createCLAHE(3.0, (8, 8)).apply(gray)
    cv2.imwrite("/tmp/ir_check.png", boost)
    print("preview: /tmp/ir_check.png")


if __name__ == "__main__":
    main()
