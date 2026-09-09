"""YuNet detection + SFace embedding, CPU-only via cv2.dnn."""
import math
import os

import cv2
import numpy as np

YUNET = "face_detection_yunet_2023mar.onnx"
SFACE = "face_recognition_sface_2021dec.onnx"


def _eye_direction(gray, center, eye_distance, min_contrast):
    """Estimate pupil displacement inside a small, fixed eye-region crop."""
    cx, cy = (float(v) for v in center)
    half_w = max(4, int(round(eye_distance * 0.22)))
    half_h = max(3, int(round(eye_distance * 0.12)))
    x0, x1 = max(0, int(round(cx)) - half_w), min(gray.shape[1], int(round(cx)) + half_w + 1)
    y0, y1 = max(0, int(round(cy)) - half_h), min(gray.shape[0], int(round(cy)) + half_h + 1)
    patch = gray[y0:y1, x0:x1]
    if patch.shape[0] < 5 or patch.shape[1] < 7:
        return None
    smooth = cv2.GaussianBlur(patch, (3, 3), 0).astype(np.float64)
    dark, light = np.percentile(smooth, (15, 70))
    contrast = float(light - dark)
    if contrast < min_contrast:
        return None
    weights = np.clip(light - smooth, 0, None)
    total = float(weights.sum())
    if total < 1e-6:
        return None
    yy, xx = np.indices(smooth.shape)
    pupil_x = float((weights * xx).sum() / total)
    pupil_y = float((weights * yy).sum() / total)
    x_offset = (pupil_x - (smooth.shape[1] - 1) / 2) / max(1, (smooth.shape[1] - 1) / 2)
    y_offset = (pupil_y - (smooth.shape[0] - 1) / 2) / max(1, (smooth.shape[0] - 1) / 2)
    return {"offset": [round(x_offset, 3), round(y_offset, 3)],
            "contrast": round(contrast, 2)}


def check_attention(img_bgr, face, cfg):
    """Check frontal head pose, eye visibility and coarse pupil direction.

    YuNet supplies eye, nose and mouth landmarks. Pupil centroids are estimated
    from local contrast, so this is an attention heuristic rather than a
    calibrated gaze tracker.
    """
    result = {"required": True, "ok": False}
    values = np.asarray(face, dtype=np.float64).flatten()
    if values.size < 15 or not np.isfinite(values[:14]).all():
        return {**result, "reason": "eyes-not-visible"}
    right_eye, left_eye, nose, right_mouth, left_mouth = values[4:14].reshape(5, 2)
    eye_axis = left_eye - right_eye
    eye_distance = float(np.linalg.norm(eye_axis))
    if eye_distance < 4:
        return {**result, "reason": "eyes-not-visible"}
    horizontal = eye_axis / eye_distance
    vertical = np.array([-horizontal[1], horizontal[0]])
    eye_mid = (right_eye + left_eye) / 2
    mouth_mid = (right_mouth + left_mouth) / 2
    if float(np.dot(mouth_mid - eye_mid, vertical)) < 0:
        vertical = -vertical
    mouth_drop = float(np.dot(mouth_mid - eye_mid, vertical))
    if mouth_drop < eye_distance * 0.2:
        return {**result, "reason": "head-pitch"}
    yaw = max(abs(float(np.dot(nose - eye_mid, horizontal)) / eye_distance),
              abs(float(np.dot(mouth_mid - eye_mid, horizontal)) / eye_distance))
    pitch = float(np.dot(nose - eye_mid, vertical)) / mouth_drop
    roll = math.degrees(math.asin(min(1.0, abs(float(horizontal[1])))))
    result.update(head_yaw=round(yaw, 3), head_pitch=round(pitch, 3),
                  roll_deg=round(roll, 2))
    if yaw > cfg["max_head_yaw"]:
        return {**result, "reason": "head-turned"}
    if roll > cfg["max_roll_deg"]:
        return {**result, "reason": "head-tilted"}
    if not cfg["min_head_pitch"] <= pitch <= cfg["max_head_pitch"]:
        return {**result, "reason": "head-pitch"}
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    eyes = [_eye_direction(gray, point, eye_distance, cfg["min_eye_contrast"])
            for point in (right_eye, left_eye)]
    if any(eye is None for eye in eyes):
        return {**result, "reason": "eyes-not-visible"}
    result["eyes"] = eyes
    if any(max(abs(value) for value in eye["offset"]) > cfg["max_eye_offset"]
           for eye in eyes):
        return {**result, "reason": "eyes-looking-away"}
    return {**result, "ok": True, "reason": "ok"}


def load_models(model_dir):
    yunet_p = os.path.join(model_dir, YUNET)
    sface_p = os.path.join(model_dir, SFACE)
    for p in (yunet_p, sface_p):
        if not os.path.exists(p):
            raise FileNotFoundError(f"missing model {p}; run setup_models.py")
    det = cv2.FaceDetectorYN_create(yunet_p, "", (320, 320))
    rec = cv2.FaceRecognizerSF_create(sface_p, "")
    return det, rec


def embed(img_bgr, det, rec, min_score=0.6, attention=None, details=False):
    """Return a single-face embedding, score and box.

    With ``details=True``, append attention details as a fourth value. The box
    is (x, y, w, h) of the best detection for quality gating.
    """
    h, w = img_bgr.shape[:2]
    det.setInputSize((w, h))
    det.setScoreThreshold(float(min_score))
    _, faces = det.detect(img_bgr)
    if faces is None or len(faces) == 0:
        result = (None, 0.0, None)
        return (*result, None) if details else result
    best = max(faces, key=lambda f: float(f[-1]))
    score = float(best[-1])
    box = tuple(float(v) for v in best[:4])
    if len(faces) != 1:
        result = (None, score, box)
        return (*result, None) if details else result
    if score < min_score:
        result = (None, score, box)
        return (*result, None) if details else result
    attention_detail = None
    if attention and attention["enabled"]:
        attention_detail = check_attention(img_bgr, best, attention)
        if not attention_detail["ok"]:
            result = (None, score, box)
            return (*result, attention_detail) if details else result
    aligned = rec.alignCrop(img_bgr, best)
    feat = rec.feature(aligned)
    v = np.asarray(feat, dtype=np.float64).flatten()
    norm = np.linalg.norm(v)
    if v.shape != (128,) or not np.isfinite(v).all() or norm < 1e-12:
        raise ValueError("invalid SFace embedding")
    v /= norm
    result = (v, score, box)
    return (*result, attention_detail) if details else result


def similarity(a, b):
    value = float(np.dot(a, b))
    if not np.isfinite(value):
        raise ValueError("non-finite similarity")
    return value
