"""Bounded JPEG snapshots shared by enrollment and verification."""

import base64
import binascii

import cv2
import numpy as np

MAX_JPEG_BYTES = 512 * 1024


def jpeg(frame, face=None):
    if frame is None or not frame.size:
        raise ValueError("No camera image is available.")
    if face is not None:
        x, y, w, h = map(int, face[:4])
        margin = int(max(w, h) * 0.2)
        x1, y1 = max(0, x - margin), max(0, y - margin)
        x2, y2 = (
            min(frame.shape[1], x + w + margin),
            min(frame.shape[0], y + h + margin),
        )
        if x2 > x1 and y2 > y1:
            frame = frame[y1:y2, x1:x2]
    scale = min(1, 640 / max(frame.shape[:2]))
    if scale < 1:
        frame = cv2.resize(
            frame,
            (
                max(1, round(frame.shape[1] * scale)),
                max(1, round(frame.shape[0] * scale)),
            ),
        )
    ok, data = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok or data.nbytes > MAX_JPEG_BYTES:
        raise ValueError("Could not encode a bounded capture image.")
    return data.tobytes()


def validate_jpeg(raw):
    if (
        not isinstance(raw, bytes)
        or not 0 < len(raw) <= MAX_JPEG_BYTES
        or not raw.startswith(b"\xff\xd8")
    ):
        raise ValueError("Invalid JPEG image.")
    frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if frame is None or max(frame.shape[:2]) > 1280:
        raise ValueError("Invalid JPEG dimensions.")
    return raw


def decode_image(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > ((MAX_JPEG_BYTES + 2) // 3) * 4:
        raise ValueError("Image exceeds the capture limit.")
    try:
        return validate_jpeg(base64.b64decode(value, validate=True))
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Send a valid base64 JPEG image.") from exc


def normalize_image(raw):
    if not raw or len(raw) > 2 * 1024 * 1024:
        raise ValueError("Registration photo is missing or too large.")
    frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if frame is None or max(frame.shape[:2]) > 1280:
        raise ValueError("Registration photo is invalid.")
    return jpeg(frame)
