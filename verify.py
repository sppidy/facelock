#!/usr/bin/env python3
"""Verify current face. Exit 0 = match. RGB first, IR flash second."""
import argparse
import getpass
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from facelock import capture, ir_capture, recognize, store

HERE = os.path.dirname(os.path.abspath(__file__))


def load_cfg():
    for p in ("/etc/facelock/config.yaml",
              os.path.join(HERE, "..", "config.yaml"),
              os.path.join(HERE, "config.yaml")):
        if os.path.exists(p):
            with open(p) as f:
                return yaml.safe_load(f)
    sys.exit("no config.yaml found")


def attempt(det, rec, cfg, camera, ref, thresh, use_ir):
    cap = cfg["capture"]
    try:
        if use_ir:
            img = ir_capture.snapshot_ir(
                camera, cfg["ir_led"]["path"],
                cfg["ir_led"]["brightness"],
                cfg["ir_capture"]["frames"],
                timeout=cap["timeout_sec"],
                strobe_path=cfg["ir_led"].get("strobe_path"))
        else:
            img = capture.snapshot(camera, cap["width"], cap["height"],
                                   cap.get("warmup_sec", 1.5),
                                   timeout=cap["timeout_sec"])
    except Exception as e:
        print(f"capture failed: {e}")
        return False, 0.0, 0.0
    v, s = recognize.embed(img, det, rec, cfg["match"]["detector_min_score"])
    if v is None or ref is None:
        return False, 0.0, s
    sim = recognize.similarity(v, ref)
    return sim >= thresh, sim, s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default=os.environ.get("SUDO_USER")
                    or getpass.getuser())
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    cfg = load_cfg()
    refs = store.load(cfg["store"]["dir"], a.user)
    if not refs:
        print(f"no enrollment for {a.user}")
        return 1
    det, rec = recognize.load_models(cfg["models"]["dir"])
    m = cfg["match"]
    fusion = cfg.get("fusion", {}).get("mode", "fallback")
    rgb_ok, rgb_sim, rgb_s = attempt(
        det, rec, cfg, cfg["cameras"]["rgb"],
        refs.get("rgb"), m["rgb_threshold"], False)
    print(f"rgb: face_score={rgb_s:.2f} similarity={rgb_sim:.2f} "
          f"-> {'MATCH' if rgb_ok else 'no match'}")
    ir_ok = False
    if refs.get("ir") is not None and cfg["cameras"].get("ir") != "auto":
        if fusion == "both" or not rgb_ok:
            ir_ok, ir_sim, ir_s = attempt(
                det, rec, cfg, cfg["cameras"]["ir"],
                refs.get("ir"), m["ir_threshold"], True)
            print(f"ir: face_score={ir_s:.2f} similarity={ir_sim:.2f} "
                  f"-> {'MATCH' if ir_ok else 'no match'}")
    ok = rgb_ok or ir_ok
    if a.quiet:
        pass
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
