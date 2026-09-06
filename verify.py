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


def flash_state(cand_paths):
    vals = {}
    for key, p in cand_paths.items():
        try:
            with open(p) as f:
                vals[key] = f.read().strip()
        except OSError:
            vals[key] = "?"
    return vals


def attempt_dual(det, rec, cfg, refs):
    """Concurrent RGB+IR in one process (staged stack). Returns dict of
    (ok, sim, score) per source. Raises to trigger legacy fallback."""
    from facelock import dual_capture
    from facelock.ir_capture import fire_strobe
    import os
    m = cfg["match"]
    d = cfg.get("dual", {})
    led = cfg["ir_led"]
    strobe = os.path.dirname(led.get("strobe_path", ""))
    pre = flash_state({"fault": strobe + "/flash_fault",
                       "strobe": strobe + "/flash_strobe"}) if strobe else {}
    fired = {}

    def fire():
        fire_strobe(led.get("strobe_path"), led["path"], led["brightness"])
        try:
            with open(led.get("strobe_path", "")) as f:
                fired["readback"] = f.read().strip()
        except OSError:
            fired["readback"] = "?"

    imgs = dual_capture.capture_dual(
        cfg["cameras"]["rgb"], cfg["cameras"]["ir"],
        rgb_size=(d.get("rgb_width", 640), d.get("rgb_height", 480)),
        nbuf=d.get("buffers", 8), on_streaming=fire)
    import numpy as np
    post = flash_state({"fault": strobe + "/flash_fault",
                        "strobe": strobe + "/flash_strobe"}) if strobe else {}
    ir_mean = round(float(np.asarray(imgs["ir"]).mean()), 1) \
        if imgs.get("ir") is not None else -1
    print(f"flash pre={pre} post={post} fired_rb={fired.get('readback', '?')} "
          f"ir_mean={ir_mean}")
    res = {}
    for src, thresh in (("rgb", m["rgb_threshold"]),
                        ("ir", m["ir_threshold"])):
        img, ref = imgs.get(src), refs.get(src)
        if img is None or ref is None:
            res[src] = (False, 0.0, 0.0)
            continue
        v, s = recognize.embed(img, det, rec,
                               cfg["match"]["detector_min_score"])
        if v is None:
            res[src] = (False, 0.0, s)
        else:
            sim = recognize.similarity(v, ref)
            res[src] = (sim >= thresh, sim, s)
    return res


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
    dual = None
    if os.environ.get("FACELOCK_STAGED"):
        try:
            dual = attempt_dual(det, rec, cfg, refs)
        except Exception as e:
            print(f"dual path failed, legacy fallback: {e}")
    if dual is not None:
        # concurrent path always evaluates both sources; scores always
        # logged (pam.log is root-only) so misses are diagnosable
        for src in ("rgb", "ir"):
            ok, sim, s = dual[src]
            print(f"{src}: face_score={s:.2f} similarity={sim:.2f} "
                  f"-> {'MATCH' if ok else 'no match'}")
        return 0 if (dual["rgb"][0] or dual["ir"][0]) else 1
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
