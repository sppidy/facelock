"""Shared feature extraction, illumination checks, and explicit domain policy."""
import hashlib
import json

import numpy as np

from . import acquisition, liveness, quality, recognize


def enrollment_metadata(cfg):
    fields = {"version": "illumination-v2", "cameras": cfg["cameras"],
              "torch": cfg["ir_led"], "required": cfg["auth"]["required_sensors"],
              "mode": cfg["capture"]["mode"], "runtime": cfg["runtime"],
              "rgb_size": [cfg["dual"]["rgb_width"], cfg["dual"]["rgb_height"]],
              "resize": {"method": "fit-area-v1", "width": cfg["capture"]["width"],
                         "height": cfg["capture"]["height"]}}
    return {"version": "illumination-v2", "capture_signature": hashlib.sha256(
        json.dumps(fields, sort_keys=True).encode()).hexdigest()}


def extract(cfg, det, rec, burst):
    vectors, sources, boxes = {}, {}, {}
    for source in cfg["auth"]["required_sensors"]:
        usable, details = [], []
        for img in burst.images.get(source, []):
            v, score, box = recognize.embed(img, det, rec, cfg["match"]["detector_min_score"])
            qok, reasons = quality.check(img, box, cfg["quality"])
            details.append({"score": round(score, 3), "quality": qok,
                            "reason": reasons if v is not None else "no-single-face",
                            "mean": round(quality.brightness(img), 2)})
            if v is not None and qok:
                usable.append(v)
                boxes[source] = box
        sources[source] = {"samples": details, "usable": len(usable), "match": False}
        if len(usable) >= 2:
            vectors[source] = usable
    live = {"required": "ir" in cfg["auth"]["required_sensors"]}
    if live["required"]:
        box = boxes.get("ir")
        if box is None or not burst.ir_frames:
            live.update(ok=False, reason="no-ir-face")
        else:
            ih, iw = burst.ir_frames[0].shape
            x, y, w, h = box
            x0, y0 = max(0, int(x)), max(0, int(y))
            roi = (x0, y0, max(0, min(iw, int(x + w)) - x0),
                   max(0, min(ih, int(y + h)) - y0))
            ok, detail = liveness.check_challenge(burst.ir_frames, burst.pattern,
                                                  cfg["challenge"]["min_gap"], roi)
            live.update(ok=ok, **detail)
        live["texture"] = liveness.temporal_noise(
            [f for f, p in zip(burst.ir_frames, burst.pattern) if p])
    else:
        live["ok"] = True
    return vectors, {"sources": sources, "illumination": live, "capture": burst.stats}


def verify(cfg, refs, metadata, *, capture_fn=acquisition.capture, models_fn=recognize.load_models):
    required = cfg["auth"]["required_sensors"]
    if any(s not in refs for s in required):
        return False, {"reason": "missing-enrollment", "required": required}
    if metadata != enrollment_metadata(cfg):
        return False, {"reason": "capture-settings-changed-reenroll"}
    det, rec = models_fn(cfg["models"]["dir"])
    vectors, detail = extract(cfg, det, rec, capture_fn(cfg))
    for source in required:
        similarities = [recognize.similarity(v, refs[source]) for v in vectors.get(source, [])]
        # Two independent good frames must match in every configured domain.
        threshold = cfg["match"][source + "_threshold"]
        matched = sum(s >= threshold for s in similarities) >= 2
        detail["sources"][source].update(match=matched,
            similarities=[round(s, 4) for s in similarities], threshold=threshold)
    accepted, fusion = liveness.fuse(detail["sources"], required)
    accepted = accepted and detail["illumination"]["ok"]
    detail.update(fusion=fusion, accepted=accepted)
    return accepted, detail


def enroll(cfg, *, capture_fn=acquisition.capture, models_fn=recognize.load_models):
    det, rec = models_fn(cfg["models"]["dir"])
    vectors, detail = extract(cfg, det, rec, capture_fn(cfg))
    if not detail["illumination"]["ok"] or any(s not in vectors for s in cfg["auth"]["required_sensors"]):
        return None, detail
    out = {}
    for source, samples in vectors.items():
        # Prevent averaging unrelated samples into a new enrollment.
        if min(recognize.similarity(a, b) for a in samples for b in samples) < cfg["match"][source + "_threshold"]:
            detail["reason"] = f"{source}-inconsistent-enrollment"
            return None, detail
        mean = np.mean(samples, axis=0)
        norm = np.linalg.norm(mean)
        if not np.isfinite(norm) or norm < 1e-12:
            raise ValueError("invalid averaged enrollment")
        out[source] = mean / norm
    return out, detail
