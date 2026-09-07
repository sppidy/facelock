#!/usr/bin/env python3
"""Verify current face. Exit 0 = match. RGB first, IR flash second."""
import argparse
import getpass
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from facelock import capture, ir_capture, recognize, store, telemetry

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
    (ok, sim, score) per source + diag. Raises to trigger legacy fallback."""
    from facelock import dual_capture, liveness
    from facelock.ir_capture import set_led
    import os
    m = cfg["match"]
    d = cfg.get("dual", {})
    led = cfg["ir_led"]
    strobe = os.path.dirname(led.get("strobe_path", ""))
    pre = flash_state({"fault": strobe + "/flash_fault",
                       "strobe": strobe + "/flash_strobe"}) if strobe else {}
    nbuf = d.get("buffers", 8)

    # --- M2: per-attempt strobe challenge -----------------------------
    nonce = liveness.make_nonce()
    pattern = liveness.challenge_pattern(nonce, nbuf)
    driver = liveness.StrobeDriver(led.get("strobe_path"),
                                   led.get("path"), pattern)
    ir_burst = {}   # frame_idx -> gray 2-D array
    lit_flags = {}  # frame_idx -> bool (strobe commanded on)

    # NOTE: dual_capture hands raw FrameBuffers to frame_cb; decode inline
    # is heavy. Instead collect per-frame decode AFTER capture via the
    # same mmaps the final decode uses. Simpler: re-capture is wasteful;
    # so we decode each IR buffer once here (few hundred KB each).
    def frame_cb_raw(name, idx, fb):
        if name != "ir":
            return
        try:
            import numpy as np
            from facelock.ir_capture import unpack_r10_bytes
            planes = dual_capture._planes(fb)
            blob = dual_capture._read_plane(planes[0])
            h = int(360)  # hm1092 fixed geometry
            stride = len(blob) // h
            gray = unpack_r10_bytes(blob, 560, h, stride)
            ir_burst[idx] = gray
            lit_flags[idx] = driver.on_frame(idx)
        except Exception:
            pass

    try:
        imgs, stats = dual_capture.capture_dual_stats(
            cfg["cameras"]["rgb"], cfg["cameras"]["ir"],
            rgb_size=(d.get("rgb_width", 640), d.get("rgb_height", 480)),
            nbuf=nbuf,
            settle=d.get("settle"),
            frame_cb=frame_cb_raw)
    finally:
        driver.off()
    import numpy as np
    post = flash_state({"fault": strobe + "/flash_fault",
                        "strobe": strobe + "/flash_strobe"}) if strobe else {}
    ir_mean = round(float(np.asarray(imgs["ir"]).mean()), 1) \
        if imgs.get("ir") is not None else -1

    # --- M2 verify: challenge + texture --------------------------------
    burst = [ir_burst[i] for i in sorted(ir_burst)] if ir_burst else []
    flags = [lit_flags[i] for i in sorted(ir_burst)] if ir_burst else []
    chal_ok, chal_detail = (False, {"reason": "no-burst"})
    if burst and flags and len(burst) == len(flags):
        chal_ok, chal_detail = liveness.check_challenge(burst, flags)
    tex_score, tex_detail = liveness.temporal_noise(burst if chal_ok else [])
    print(f"challenge ok={chal_ok} {chal_detail}")
    print(f"texture score={tex_score} {tex_detail}")
    print(f"flash pre={pre} post={post} ir_mean={ir_mean}")

    from facelock import quality
    qcfg = cfg.get("quality", {})
    res = {}
    qinfo = {}
    expinfo = {k: {"frames": v.get("frames"), "settled": v.get("settled"),
                   "exp": v.get("exp"), "gain": v.get("gain")}
               for k, v in (stats or {}).items()}
    for src, thresh in (("rgb", m["rgb_threshold"]),
                        ("ir", m["ir_threshold"])):
        img, ref = imgs.get(src), refs.get(src)
        if img is None or ref is None:
            res[src] = (False, 0.0, 0.0)
            qinfo[src] = {"gated": "no-image"}
            continue
        v, s, box = recognize.embed(img, det, rec,
                                    cfg["match"]["detector_min_score"])
        if v is None:
            res[src] = (False, 0.0, s)
            qinfo[src] = {"gated": "no-face", "score": round(s, 2)}
        else:
            ok_q, reasons = quality.check(img, box, qcfg)
            if not ok_q:
                res[src] = (False, 0.0, s)
                qinfo[src] = {"gated": reasons, "score": round(s, 2)}
            else:
                sim = recognize.similarity(v, ref)
                res[src] = (sim >= thresh, sim, s)
                qinfo[src] = {"score": round(s, 2),
                              "blur": round(quality.blur_score(img), 1),
                              "mean": round(quality.brightness(img), 1)}
    # M2: liveness must pass for IR to count at all
    if not chal_ok:
        res["ir"] = (False, res["ir"][1], res["ir"][2])
        qinfo["ir"] = {**qinfo.get("ir", {}), "gated": "challenge-failed"}
    if tex_score < 0.5:
        res["ir"] = (False, res["ir"][1], res["ir"][2])
        qinfo["ir"] = {**qinfo.get("ir", {}), "gated": "texture"}
    liv = {"challenge": chal_ok, "challenge_detail": chal_detail,
           "texture": tex_score, "texture_detail": tex_detail}
    return res, {"quality": qinfo, "exposure": expinfo, "liveness": liv}


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
    v, s, _box = recognize.embed(img, det, rec, cfg["match"]["detector_min_score"])
    if v is None or ref is None:
        return False, 0.0, s
    sim = recognize.similarity(v, ref)
    return sim >= thresh, sim, s


VERIFY_TAG = "verify-dualfix3"


def main():
    ap = argparse.ArgumentParser()
    print(f"facelock {VERIFY_TAG}", flush=True)
    ap.add_argument("--user", default=os.environ.get("SUDO_USER")
                    or getpass.getuser())
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    cfg = load_cfg()
    import os as _os
    _sd = cfg["store"]["dir"]
    try:
        _files = sorted(_os.listdir(_sd))
    except OSError as e:
        _files = [f"LISTDIR-ERR {e}"]
    print(f"store dir={_sd} want={a.user}.npz "
          f"have={_os.path.exists(_os.path.join(_sd, a.user + '.npz'))} "
          f"files={_files}", flush=True)
    refs = store.load(cfg["store"]["dir"], a.user)
    if not refs:
        print(f"no enrollment for {a.user}")
        return 1
    det, rec = recognize.load_models(cfg["models"]["dir"])
    m = cfg["match"]
    fusion = cfg.get("fusion", {}).get("mode", "fallback")
    import time as _t
    dual = None
    diag = {}
    t_cap = 0.0
    if os.environ.get("FACELOCK_STAGED"):
        t0 = _t.time()
        try:
            dual, diag = attempt_dual(det, rec, cfg, refs)
            t_cap = _t.time() - t0
            print(f"dual capture took {t_cap:.1f}s")
        except Exception as e:
            print(f"dual path failed, legacy fallback: {e}")
    if dual is not None:
        # concurrent path always evaluates both sources; scores always
        # logged (pam.log is root-only) so misses are diagnosable
        srcs = {}
        for src in ("rgb", "ir"):
            ok, sim, s = dual[src]
            print(f"{src}: face_score={s:.2f} similarity={sim:.2f} "
                  f"-> {'MATCH' if ok else 'no match'}")
            srcs[src] = {"match": bool(ok), "sim": round(sim, 2),
                         "score": round(s, 2),
                         **(diag.get("quality", {}).get(src, {}))}
        # M2: fusion replaces OR (both domains must agree when both are live)
        from facelock import liveness as _liv
        both_req = refs.get("rgb") is not None and refs.get("ir") is not None
        ok, fus = _liv.fuse(dual.get("rgb"), dual.get("ir"),
                            both_required=both_req)
        print(f"fusion: {fus}")
        telemetry.emit({"event": "verify", "user": a.user, "path": "dual",
                        "result": "match" if ok else "miss",
                        "capture_s": round(t_cap, 2), "sources": srcs,
                        "exposure": diag.get("exposure"),
                        "liveness": {k: v for k, v in
                                     (diag.get("liveness") or {}).items()
                                     if k == "challenge"},
                        "fusion": fus})
        return 0 if ok else 1
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
    telemetry.emit({"event": "verify", "user": a.user, "path": "legacy",
                    "result": "match" if ok else "miss",
                    "sources": {
                        "rgb": {"match": bool(rgb_ok),
                                "sim": round(rgb_sim, 2),
                                "score": round(rgb_s, 2)},
                        "ir": {"match": bool(ir_ok),
                               "sim": round(ir_sim, 2) if
                               refs.get("ir") is not None else None,
                               "score": round(ir_s, 2) if
                               refs.get("ir") is not None else None}}})
    if a.quiet:
        pass
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
