#!/usr/bin/env python3
"""Capture diagnostics without reading or changing enrollment."""
import argparse
import json
from pathlib import Path

from facelock import acquisition, config, liveness, quality, recognize


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="/etc/facelock/config.yaml")
    ap.add_argument("--save-frames", type=Path)
    ap.add_argument("--camera-only", action="store_true", help="inspect imaging without loading face models")
    args = ap.parse_args()
    cfg = config.load(args.config)
    if not args.camera_only:
        det, rec = recognize.load_models(cfg["models"]["dir"])
    burst = acquisition.capture(cfg)
    result = {"stats": burst.stats, "sources": {}}
    for name, images in burst.images.items():
        result["sources"][name] = []
        for img in images:
            if args.camera_only:
                result["sources"][name].append({"size": list(img.shape[1::-1]),
                                                "mean": round(quality.brightness(img), 2),
                                                "bgr_mean": img.mean(axis=(0, 1)).round(2).tolist()})
                continue
            vector, score, box = recognize.embed(img, det, rec, cfg["match"]["detector_min_score"])
            qok, reasons = quality.check(img, box, cfg["quality"])
            result["sources"][name].append({"score": round(score, 3), "face": vector is not None,
                                            "box": box, "quality": qok, "reasons": reasons,
                                            "mean": round(quality.brightness(img), 2)})
        if args.save_frames and images:
            import cv2
            args.save_frames.mkdir(mode=0o700, parents=True, exist_ok=True)
            cv2.imwrite(str(args.save_frames / (name + ".png")), images[-1])
    if burst.ir_frames:
        ok, detail = liveness.check_challenge(burst.ir_frames, burst.pattern, cfg["challenge"]["min_gap"])
        result["challenge"] = {"ok": ok, **detail}
        result["texture"] = liveness.temporal_noise([f for f, p in zip(burst.ir_frames, burst.pattern) if p])
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
