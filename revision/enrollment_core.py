"""Enrollment checks preserved from the original working Face Studio."""

import os

import cv2
import numpy as np

THRESHOLD = float(os.environ.get("ARCFACE_MATCH_THRESHOLD", "0.60"))
BLUR_THRESHOLD = float(os.environ.get("FACE_STUDIO_BLUR_THRESHOLD", "20"))
if not 0 < THRESHOLD < 1:
    raise ValueError("ARCFACE_MATCH_THRESHOLD must be between 0 and 1.")
if not np.isfinite(BLUR_THRESHOLD) or BLUR_THRESHOLD < 0:
    raise ValueError("FACE_STUDIO_BLUR_THRESHOLD must be finite and non-negative.")


def sharpness_score(gray):
    """Measure edges at a fixed scale so larger crops aren't penalized."""
    normalized = cv2.resize(gray, (128, 128), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(normalized, cv2.CV_64F).var())


def face_quality(frame, face):
    """Apply the same quality checks to enrollment and live identification."""
    height, width = frame.shape[:2]
    x, y, w, h = face[:4].astype(int)
    if min(w, h) < 100 or x < 0 or y < 0 or x + w > width or y + h > height:
        return "Move closer and keep your entire face visible."
    if face[14] < 0.90:
        return "Face confidence is low. Use even lighting and turn slightly less."
    crop = frame[y : y + h, x : x + w]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if gray.mean() < 45:
        return "Add more light in front of your face."
    sharpness = sharpness_score(gray)
    if sharpness < BLUR_THRESHOLD:
        return (
            f"Image is too soft (sharpness {sharpness:.1f}; minimum {BLUR_THRESHOLD:g}). "
            "Hold still or improve lighting."
        )
    return None


def pose_ratios(face):
    """Coarse pose cues from five facial landmarks, not calibrated degrees."""
    eyes = face[4:8].reshape(2, 2)
    nose = face[8:10]
    mouth = face[10:14].reshape(2, 2).mean(axis=0)
    midpoint = eyes.mean(axis=0)
    span = max(float(np.linalg.norm(eyes[0] - eyes[1])), 1.0)
    horizontal = (eyes[np.argmax(eyes[:, 0])] - eyes[np.argmin(eyes[:, 0])]) / span
    vertical = np.array([-horizontal[1], horizontal[0]])
    yaw = float(np.dot(nose - midpoint, horizontal) / span)
    pitch = float(
        np.dot(nose - midpoint, vertical) / max(np.dot(mouth - midpoint, vertical), 1.0)
    )
    return yaw, pitch


def pose_matches(stage, yaw, pitch, baseline):
    dy, dp = yaw - baseline[0], pitch - baseline[1]
    return {
        "front": abs(yaw) < 0.16,
        "left": dy > 0.15,
        "right": dy < -0.15,
        "up": abs(dy) < 0.18 and dp < -0.07,
        "down": abs(dy) < 0.18 and dp > 0.07,
    }[stage]
