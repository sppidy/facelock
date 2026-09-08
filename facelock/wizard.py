"""Collect three consistent enrollment samples, retrying rejected bursts."""
import numpy as np

from . import auth, cli, recognize

SAMPLES = 3


def collect(cfg, progress, *, enroll_fn=auth.enroll, models_fn=recognize.load_models,
            cancelled=lambda: False):
    models = models_fn(cfg["models"]["dir"])
    kept = []
    history = []
    required = cfg["auth"]["required_sensors"]
    for attempt in range(1, cfg["enrollment"]["max_bursts"] + 1):
        if cancelled():
            return None, {"reason": "enrollment-cancelled"}
        progress({"event": "scanning", "burst": attempt, "collected": len(kept),
                  "target": SAMPLES})
        try:
            vectors, detail = enroll_fn(cfg, models_fn=lambda _: models)
        except cli.AttemptTerminated:
            raise
        except (OSError, RuntimeError) as exc:
            vectors, detail = None, {"reason": str(exc)}
        if cancelled():
            return None, {"reason": "enrollment-cancelled"}
        if vectors is not None and any(
                recognize.similarity(vectors[s], prior[s]) < cfg["match"][s + "_threshold"]
                for prior in kept for s in required):
            vectors = None
            detail["reason"] = "inconsistent-face-keep-the-same-person-in-view"
        if vectors is not None:
            kept.append(vectors)
        event = {"event": "burst", "burst": attempt, "collected": len(kept),
                 "target": SAMPLES, "usable": vectors is not None, **detail}
        history.append(event)
        progress(event)
        if len(kept) == SAMPLES:
            result = {}
            for s in required:
                mean = np.mean([sample[s] for sample in kept], axis=0)
                norm = np.linalg.norm(mean)
                if not np.isfinite(norm) or norm < 1e-12:
                    raise ValueError("invalid enrollment average")
                result[s] = mean / norm
            return result, {"bursts": history, "collected": SAMPLES}
    return None, {"bursts": history, "collected": len(kept),
                  "reason": "not-enough-quality-samples"}


def describe(event):
    if event["event"] == "scanning":
        return f"Burst {event['burst']}: look at the camera ({event['collected']}/3 collected)"
    lines = []
    for sensor, source in event.get("sources", {}).items():
        for index, sample in enumerate(source["samples"], 1):
            verdict = "ok" if sample.get("face") and sample["quality"] else sample["reason"]
            lines.append(f"  {sensor} frame {index}: score={sample['score']:.2f} "
                         f"blur={sample['blur']:.1f} brightness={sample['mean']:.1f} {verdict}")
            if "depth" in sample:
                measured = sample["depth"]
                lines.append(f"    depth: {measured['reason']}"
                             + (f" relief={measured['relief_m']:.3f}m"
                                if "relief_m" in measured else ""))
    lines.append(f"Collected {event['collected']}/3 samples." if event["usable"] else
                 f"Retrying: {event.get('reason', 'face quality or illumination check failed')}.")
    return "\n".join(lines)
