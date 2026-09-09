"""Shared feature extraction, illumination checks, and explicit domain policy."""
import hashlib
import json

import numpy as np

from . import acquisition, depth, liveness, quality, recognize


def enrollment_metadata(cfg):
    # The capability flag only requires a kernel attestation before capture;
    # it does not change the camera pipeline or resulting embeddings.
    runtime = {key: cfg["runtime"][key] for key in ("stack", "tuning")}
    fields = {"version": "illumination-v2", "cameras": cfg["cameras"],
              "torch": cfg["ir_led"], "required": cfg["auth"]["required_sensors"],
              "mode": cfg["capture"]["mode"], "runtime": runtime,
              "rgb_size": [cfg["dual"]["rgb_width"], cfg["dual"]["rgb_height"]],
              "resize": {"method": "fit-area-v1", "width": cfg["capture"]["width"],
                         "height": cfg["capture"]["height"]}}
    if cfg["depth"]["enabled"]:
        from .stereo import load_calibration
        _, digest = load_calibration(cfg)
        fields["depth"] = {"version": "measured-relief-v1", **cfg["depth"],
                           "calibration_sha256": digest}
    return {"version": "illumination-v2", "capture_signature": hashlib.sha256(
        json.dumps(fields, sort_keys=True).encode()).hexdigest()}


def extract(cfg, det, rec, burst):
    vectors, sources, boxes = {}, {}, {}
    for source in cfg["auth"]["required_sensors"]:
        usable, details = [], []
        for index, img in enumerate(burst.images.get(source, [])):
            attention_cfg = cfg["attention"] if source == "rgb" else None
            v, score, box, attention = recognize.embed(
                img, det, rec, cfg["match"]["detector_min_score"],
                attention=attention_cfg, details=True)
            qok, reasons = quality.check(img, box, cfg["quality"])
            reason = reasons if v is not None else "no-single-face"
            if attention is not None and not attention["ok"]:
                reason = attention["reason"]
            details.append({"score": round(score, 3), "quality": qok,
                            "face": box is not None,
                            "blur": round(quality.blur_score(img), 2),
                            "reason": reason,
                            "mean": round(quality.brightness(img), 2)})
            if attention is not None:
                details[-1]["attention"] = attention
            depth_ok = True
            if cfg["depth"]["enabled"] and source == "rgb":
                measured = (burst.depth_frames[index] if source == "rgb" and
                            len(burst.depth_frames) == len(burst.images.get(source, [])) else None)
                depth_ok, depth_detail = depth.check(measured, img.shape, box, cfg["depth"])
                details[-1]["depth"] = depth_detail
            if v is not None and qok and depth_ok:
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
    scores = []
    for source in required:
        similarities = [recognize.similarity(v, refs[source]) for v in vectors.get(source, [])]
        # Two independent good frames must match in every configured domain.
        threshold = cfg["match"][source + "_threshold"]
        matched = sum(s >= threshold for s in similarities) >= 2
        if matched:
            scores.append(sorted(similarities, reverse=True)[1])
        detail["sources"][source].update(match=matched,
            similarities=[round(s, 4) for s in similarities], threshold=threshold)
    accepted, fusion = liveness.fuse(detail["sources"], required)
    accepted = accepted and detail["illumination"]["ok"]
    detail.update(fusion=fusion, accepted=accepted)
    if accepted:
        detail["match_score"] = min(scores)
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
