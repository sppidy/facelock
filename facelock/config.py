"""Validated configuration shared by all entry points."""
from copy import deepcopy
import math
from pathlib import Path

import yaml

DEFAULTS = {
    "auth": {"required_sensors": ["rgb", "ir"]},
    "enrollment": {"max_bursts": 5, "timeout_sec": 180},
    "feedback": {"greeter_user": ""},
    "depth": {"enabled": False, "calibration": "", "min_distance_m": 0.2, "max_distance_m": 1.5,
              "min_valid_fraction": 0.85, "min_relief_m": 0.012,
              "max_relief_m": 0.12, "min_protrusion_m": 0.008,
              "max_skew_ms": 40},
    "capture": {"mode": "dual-pycamera", "width": 640, "height": 480,
                "warmup_sec": 1.0, "timeout_sec": 15},
    "dual": {"rgb_width": 640, "rgb_height": 480, "buffers": 4,
             "settle": {"stable": 4, "exp_tol": 0.10, "gain_tol": 0.05,
                        "min_frames": 20, "max_frames": 90}},
    "challenge": {"phases": 8, "guard_ms": 100, "discard_frames": 2,
                  "samples_per_phase": 2, "min_gap": 8.0},
    "match": {"rgb_threshold": 0.38, "ir_threshold": 0.30, "detector_min_score": 0.6},
    "quality": {"min_blur": 25.0, "brightness_range": [15.0, 235.0], "min_face_frac": 0.05},
    "pam": {"user": "", "verify_timeout_sec": 20},
    "models": {"dir": "/usr/share/facelock/models"},
    "store": {"dir": "/var/lib/facelock"},
    "runtime": {"stack": "", "tuning": "", "require_camss": False},
    "ir_led": {"mode": "flash", "path": "/sys/class/leds/ir:flash/brightness", "brightness": 128,
               "strobe_path": "/sys/class/leds/ir:flash/flash_strobe"},
}


def merge(base, override):
    result = deepcopy(base)
    for k, v in override.items():
        if isinstance(result.get(k), dict) and isinstance(v, dict):
            result[k] = merge(result[k], v)
        else:
            result[k] = v
    return result


def validate(cfg):
    for key in DEFAULTS:
        if not isinstance(cfg.get(key), dict):
            raise ValueError(f"{key} must be a mapping")
    required = cfg["auth"]["required_sensors"]
    if (not isinstance(required, list) or not required or
            any(s not in ("rgb", "ir") for s in required) or
            len(required) != len(set(required))):
        raise ValueError("auth.required_sensors must list rgb and/or ir explicitly")
    for s in required:
        camera = cfg.get("cameras", {}).get(s)
        if not isinstance(camera, str) or not camera or camera == "auto":
            raise ValueError(f"configure cameras.{s}")
    limits = [(cfg["capture"], "timeout_sec", 2, 30),
              (cfg["enrollment"], "max_bursts", 3, 10),
              (cfg["enrollment"], "timeout_sec", 60, 600),
              (cfg["capture"], "warmup_sec", 0.2, 5),
              (cfg["pam"], "verify_timeout_sec", 3, 40),
              (cfg["dual"], "buffers", 2, 16),
              (cfg["challenge"], "guard_ms", 50, 1000),
              (cfg["challenge"], "discard_frames", 2, 16),
              (cfg["challenge"], "samples_per_phase", 1, 4),
              (cfg["challenge"], "min_gap", 1, 255),
              (cfg["match"], "detector_min_score", 0.1, 1)]
    limits += [(cfg["match"], s + "_threshold", 0.01, 1) for s in required]
    limits += [(cfg["depth"], "min_distance_m", 0.1, 2),
               (cfg["depth"], "max_distance_m", 0.2, 4),
               (cfg["depth"], "min_valid_fraction", 0.5, 1),
               (cfg["depth"], "min_relief_m", 0.005, 0.1),
               (cfg["depth"], "max_relief_m", 0.01, 0.2),
               (cfg["depth"], "min_protrusion_m", 0.003, 0.05),
               (cfg["depth"], "max_skew_ms", 1, 50)]
    limits += [(cfg["dual"], key, 16, 4096) for key in ("rgb_width", "rgb_height")]
    limits += [(cfg["capture"], key, 16, 4096) for key in ("width", "height")]
    settle = cfg["dual"]["settle"]
    if not isinstance(settle, dict):
        raise ValueError("dual.settle must be a mapping")
    limits += [(settle, "stable", 1, 30), (settle, "min_frames", 3, 120),
               (settle, "max_frames", 3, 300), (settle, "exp_tol", 0, 1),
               (settle, "gain_tol", 0, 10),
               (cfg["quality"], "min_blur", 0, 10000),
               (cfg["quality"], "min_face_frac", 0.001, 1)]
    for section, key, lo, hi in limits:
        value = section[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError(f"{key} must be in {lo}..{hi}")
    for section, key in [(cfg["capture"], k) for k in ("width", "height")] + [
            (cfg["dual"], k) for k in ("buffers", "rgb_width", "rgb_height")] + [
            (settle, k) for k in ("stable", "min_frames", "max_frames")] + [
            (cfg["challenge"], k) for k in ("phases", "discard_frames", "samples_per_phase")] + [
            (cfg["enrollment"], "max_bursts")]:
        if type(section[key]) is not int:
            raise ValueError(f"{key} must be an integer")
    if settle["min_frames"] > settle["max_frames"]:
        raise ValueError("min_frames exceeds max_frames")
    if cfg["capture"]["timeout_sec"] >= cfg["pam"]["verify_timeout_sec"]:
        raise ValueError("PAM timeout must exceed capture timeout")
    mode = cfg["capture"]["mode"]
    if mode not in ("dual-pycamera", "sequential", "uvc"):
        raise ValueError("capture.mode must be dual-pycamera, sequential or uvc")
    depth = cfg["depth"]
    if type(depth["enabled"]) is not bool:
        raise ValueError("depth.enabled must be a boolean")
    if (depth["min_distance_m"] >= depth["max_distance_m"] or
            depth["min_relief_m"] >= depth["max_relief_m"]):
        raise ValueError("depth minimum must be less than maximum")
    if not isinstance(depth["calibration"], str) or (depth["calibration"] and
            not Path(depth["calibration"]).is_absolute()):
        raise ValueError("depth.calibration must be an absolute path")
    if depth["enabled"] and (mode != "dual-pycamera" or set(required) != {"rgb", "ir"}
                             or not depth["calibration"]):
        raise ValueError("depth needs concurrent rgb+ir capture and a calibration file")
    if mode == "uvc" and (required != ["rgb"] or not cfg["cameras"]["rgb"].startswith("uvc:/dev/")):
        raise ValueError("uvc mode requires only rgb and a uvc:/dev/... camera")
    if "ir" in required:
        if cfg["ir_led"]["mode"] not in ("flash", "torch"):
            raise ValueError("ir_led.mode must be flash or torch")
        if type(cfg["ir_led"]["brightness"]) is not int or cfg["ir_led"]["brightness"] < 1:
            raise ValueError("ir_led.brightness must be a positive integer")
        for key in ("path", "strobe_path"):
            value = cfg["ir_led"].get(key)
            if not isinstance(value, str) or not value.startswith("/sys/class/leds/") or ".." in Path(value).parts:
                raise ValueError(f"ir_led.{key} must be a /sys/class/leds/ path")
    bounds = cfg["quality"]["brightness_range"]
    if (not isinstance(bounds, list) or len(bounds) != 2 or
            any(type(v) not in (int, float) or not math.isfinite(v) for v in bounds) or
            not 0 <= bounds[0] < bounds[1] <= 255):
        raise ValueError("quality.brightness_range must be [low, high] within 0..255")
    for section, key in (("models", "dir"), ("store", "dir"),
                         ("runtime", "stack"), ("runtime", "tuning")):
        value = cfg[section][key]
        if not isinstance(value, str) or (value and not Path(value).is_absolute()):
            raise ValueError(f"{section}.{key} must be an absolute path")
        if key == "dir" and not value:
            raise ValueError(f"{section}.{key} is required")
    if not isinstance(cfg["pam"]["user"], str):
        raise ValueError("pam.user must be a string")
    if type(cfg["runtime"]["require_camss"]) is not bool:
        raise ValueError("runtime.require_camss must be a boolean")
    if any("/base/soc@0/cci@" in cfg["cameras"][s] for s in required):
        if not cfg["runtime"]["require_camss"]:
            raise ValueError("A14 cameras require runtime.require_camss: true")
    greeter = cfg["feedback"]["greeter_user"]
    if not isinstance(greeter, str):
        raise ValueError("feedback.greeter_user must be an account name")
    if greeter:
        from .store import valid_user
        valid_user(greeter)
    from .liveness import challenge_pattern
    challenge_pattern("validate", cfg["challenge"]["phases"])
    return cfg


def load(path="/etc/facelock/config.yaml"):
    data = yaml.safe_load(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError("configuration must be a mapping")
    return validate(merge(DEFAULTS, data))
