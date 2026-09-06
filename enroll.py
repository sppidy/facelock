#!/usr/bin/env python3
"""Enroll face embeddings. Run with sudo (writes root-owned store)."""
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


def enroll_one(det, rec, cfg, camera, use_led, label):
    cap = cfg["capture"]
    if use_led:
        capture.led(cfg["ir_led"]["path"], cfg["ir_led"]["brightness"])
    try:
        vecs = []
        for i in range(3):
            img = capture.snapshot(camera, cap["width"], cap["height"],
                                   cap.get("warmup_sec", 1.5))
            v, s = recognize.embed(img, det, rec,
                                   cfg["match"]["detector_min_score"])
            print(f"[{label}] sample {i + 1}: face_score={s:.2f} "
                  f"{'ok' if v is not None else 'NO FACE'}", flush=True)
            if v is not None:
                vecs.append(v)
    finally:
        if use_led:
            capture.led(cfg["ir_led"]["path"], 0)
    if not vecs:
        return None
    import numpy as np
    m = np.mean(vecs, axis=0)
    return m / (np.linalg.norm(m) + 1e-12)


def enroll_one_ir(det, rec, cfg):
    vecs = []
    for i in range(3):
        try:
            img = ir_capture.snapshot_ir(
                cfg["cameras"]["ir"],
                cfg["ir_led"]["path"], cfg["ir_led"]["brightness"],
                cfg["ir_capture"]["frames"],
                strobe_path=cfg["ir_led"].get("strobe_path"))
        except Exception as e:
            print(f"[ir] sample {i + 1}: capture failed: {e}", flush=True)
            continue
        v, s = recognize.embed(img, det, rec,
                               cfg["match"]["detector_min_score"])
        print(f"[ir] sample {i + 1}: face_score={s:.2f} "
              f"{'ok' if v is not None else 'NO FACE'}", flush=True)
        if v is not None:
            vecs.append(v)
    if not vecs:
        return None
    import numpy as np
    m = np.mean(vecs, axis=0)
    return m / (np.linalg.norm(m) + 1e-12)


def enroll_dual(det, rec, cfg):
    """3 concurrent bursts -> mean embedding per source. Returns dict."""
    from facelock import dual_capture
    from facelock.ir_capture import fire_strobe
    import numpy as np
    d = cfg.get("dual", {})
    acc = {"rgb": [], "ir": []}
    for i in range(3):
        try:
            fire_strobe(cfg["ir_led"].get("strobe_path"),
                        cfg["ir_led"]["path"], cfg["ir_led"]["brightness"])
            imgs = dual_capture.capture_dual(
                cfg["cameras"]["rgb"], cfg["cameras"]["ir"],
                rgb_size=(d.get("rgb_width", 640), d.get("rgb_height", 480)),
                nbuf=d.get("buffers", 8))
        except Exception as e:
            print(f"[dual] burst {i + 1}: capture failed: {e}", flush=True)
            continue
        for src in ("rgb", "ir"):
            v, s = recognize.embed(imgs[src], det, rec,
                                   cfg["match"]["detector_min_score"]) \
                if imgs.get(src) is not None else (None, 0.0)
            print(f"[{src}] sample {i + 1}: face_score={s:.2f} "
                  f"{'ok' if v is not None else 'NO FACE'}", flush=True)
            if v is not None:
                acc[src].append(v)
    out = {}
    for src, vecs in acc.items():
        if vecs:
            m = np.mean(vecs, axis=0)
            out[src] = m / (np.linalg.norm(m) + 1e-12)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sensor", choices=["rgb", "ir", "both"], default="both")
    ap.add_argument("--user", default=os.environ.get("SUDO_USER")
                    or getpass.getuser())
    a = ap.parse_args()
    cfg = load_cfg()
    det, rec = recognize.load_models(cfg["models"]["dir"])
    if a.sensor == "both" and os.environ.get("FACELOCK_STAGED"):
        try:
            out = enroll_dual(det, rec, cfg)
        except Exception as e:
            print(f"[dual] failed, legacy fallback: {e}")
            out = {}
        if out:
            store.save(cfg["store"]["dir"], a.user, **out)
            print(f"enrolled {a.user}: {', '.join(out)} (dual)")
            return
        print("[dual] no usable samples, trying legacy paths")
    out = {}
    if a.sensor in ("rgb", "both"):
        try:
            v = enroll_one(det, rec, cfg, cfg["cameras"]["rgb"], False, "rgb")
        except Exception as e:
            print(f"[rgb] failed: {e}")
            v = None
        if v is not None:
            out["rgb"] = v
    if a.sensor in ("ir", "both"):
        if cfg["cameras"].get("ir", "auto") == "auto":
            print("[ir] skipped: no camera configured (see detect_cameras.sh)")
        else:
            try:
                v = enroll_one_ir(det, rec, cfg)
            except Exception as e:
                print(f"[ir] failed: {e}")
                v = None
            if v is not None:
                out["ir"] = v
    if not out:
        sys.exit("enroll failed: no usable face samples")
    store.save(cfg["store"]["dir"], a.user, **out)
    print(f"enrolled {a.user}: {', '.join(out)}")


if __name__ == "__main__":
    main()
