"""YuNet detection + SFace embedding, CPU-only via cv2.dnn."""
import os

import cv2
import numpy as np

YUNET = "face_detection_yunet_2023mar.onnx"
SFACE = "face_recognition_sface_2021dec.onnx"


def load_models(model_dir):
    yunet_p = os.path.join(model_dir, YUNET)
    sface_p = os.path.join(model_dir, SFACE)
    for p in (yunet_p, sface_p):
        if not os.path.exists(p):
            raise FileNotFoundError(f"missing model {p}; run setup_models.py")
    det = cv2.FaceDetectorYN_create(yunet_p, "", (320, 320))
    rec = cv2.FaceRecognizerSF_create(sface_p, "")
    return det, rec


def embed(img_bgr, det, rec, min_score=0.6):
    """Single-face embedding. Returns (vector|None, detector_score, box|None).

    box is (x, y, w, h) of the best detection for quality gating.
    """
    h, w = img_bgr.shape[:2]
    det.setInputSize((w, h))
    det.setScoreThreshold(float(min_score))
    _, faces = det.detect(img_bgr)
    if faces is None or len(faces) == 0:
        return None, 0.0, None
    best = max(faces, key=lambda f: float(f[-1]))
    score = float(best[-1])
    box = tuple(float(v) for v in best[:4])
    if len(faces) != 1:
        return None, score, box
    if score < min_score:
        return None, score, box
    aligned = rec.alignCrop(img_bgr, best)
    feat = rec.feature(aligned)
    v = np.asarray(feat, dtype=np.float64).flatten()
    norm = np.linalg.norm(v)
    if v.shape != (128,) or not np.isfinite(v).all() or norm < 1e-12:
        raise ValueError("invalid SFace embedding")
    v /= norm
    return v, score, box


def similarity(a, b):
    value = float(np.dot(a, b))
    if not np.isfinite(value):
        raise ValueError("non-finite similarity")
    return value
