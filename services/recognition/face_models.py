"""RetinaFace R34 and InsightFace ArcFace R50 ONNX inference.

Decoding follows yakhyo/retinaface-pytorch; recognition preprocessing follows
InsightFace's w600k_r50 model. No PyTorch or InsightFace package is required.
"""
import os
from pathlib import Path

import cv2
import numpy as np

MODEL_ID = 'arcface-w600k-r50-v1'
PROFILE_NAME = 'profile_arcface.npz'
EMBEDDING_SIZE = 512
TOTAL_ENROLLMENT_SAMPLES = 40
SAMPLES_PER_POSE = 8
ARCFACE_POINTS = np.array([
    [38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
    [41.5493, 92.3655], [70.7299, 92.2041],
], dtype=np.float32)

_CLAHE = None


def _normalize_vectors(features):
    """Validate and L2-normalize a 2D array of feature vectors."""
    features = np.asarray(features, dtype=np.float32)
    if features.ndim == 1:
        features = features[None, :]
    norms = np.linalg.norm(features, axis=1, keepdims=True)
    if np.any(norms <= 1e-8) or np.any(~np.isfinite(norms)):
        raise ValueError("Cannot normalize invalid feature vector")
    return features / norms


def reference_embedding(features):
    """Normalize each sample, compute centrality-weighted mean, and normalize."""
    normalized = _normalize_vectors(features)
    initial_mean = np.mean(normalized, axis=0)
    initial_norm = float(np.linalg.norm(initial_mean))
    if initial_norm <= 1e-8 or not np.isfinite(initial_norm):
        raise ValueError("Cannot normalize mean feature vector")
    template = initial_mean / initial_norm
    if len(normalized) > 2:
        # Downweight extreme-angle or noisy outliers relative to the core consensus manifold
        weights = np.maximum(0.1, normalized @ template) ** 2
        weighted_mean = (normalized * weights[:, None]).sum(axis=0)
        weighted_norm = float(np.linalg.norm(weighted_mean))
        if weighted_norm > 1e-8 and np.isfinite(weighted_norm):
            return (weighted_mean / weighted_norm).astype(np.float32)
    return template.astype(np.float32)


def pose_exemplars(features):
    """Partition 40 enrollment embeddings into 5 normalized per-pose exemplar centroids."""
    normalized = _normalize_vectors(features)
    if len(normalized) < SAMPLES_PER_POSE:
        return reference_embedding(normalized)[None, :]
    exemplars = []
    for start in range(0, len(normalized), SAMPLES_PER_POSE):
        chunk = normalized[start : start + SAMPLES_PER_POSE]
        if len(chunk):
            exemplars.append(reference_embedding(chunk))
    return np.stack(exemplars).astype(np.float32)


def fast_nms(boxes, scores, iou_threshold=0.4):
    """Vectorized NumPy Non-Maximum Suppression on [x, y, w, h] boxes without Python list churn."""
    if not len(boxes):
        return np.empty(0, dtype=np.int32)
    x1 = boxes[:, 0]
    y1 = boxes[:, 1]
    w = np.maximum(0.0, boxes[:, 2])
    h = np.maximum(0.0, boxes[:, 3])
    x2 = x1 + w
    y2 = y1 + h
    areas = w * h
    order = np.argsort(scores)[::-1]
    keep = []
    while order.size > 0:
        idx = int(order[0])
        keep.append(idx)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(x1[idx], x1[rest])
        yy1 = np.maximum(y1[idx], y1[rest])
        xx2 = np.minimum(x2[idx], x2[rest])
        yy2 = np.minimum(y2[idx], y2[rest])
        inter_w = np.maximum(0.0, xx2 - xx1)
        inter_h = np.maximum(0.0, yy2 - yy1)
        inter = inter_w * inter_h
        union = areas[idx] + areas[rest] - inter
        iou = np.where(union > 0.0, inter / union, 0.0)
        order = rest[iou <= iou_threshold]
    return np.asarray(keep, dtype=np.int32)


def normalize_illumination(aligned):
    """Apply adaptive CLAHE on the luminance channel when crops are shadowed or backlit."""
    if aligned is None or aligned.size == 0:
        return aligned
    lab = cv2.cvtColor(aligned, cv2.COLOR_BGR2LAB)
    l_chan, a_chan, b_chan = cv2.split(lab)
    mean_l = float(l_chan.mean())
    std_l = float(l_chan.std())
    if mean_l < 75.0 or mean_l > 200.0 or std_l > 65.0:
        global _CLAHE
        if _CLAHE is None:
            _CLAHE = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
        l_chan = _CLAHE.apply(l_chan)
        return cv2.cvtColor(cv2.merge((l_chan, a_chan, b_chan)), cv2.COLOR_LAB2BGR)
    return aligned


def session(path):
    try:
        import sys
        from pathlib import Path
        if sys.platform == "win32":
            nv_root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
            if nv_root.exists():
                for bin_dir in nv_root.glob("*/bin"):
                    if bin_dir.is_dir():
                        os.add_dll_directory(str(bin_dir))
                        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")

        import onnxruntime as ort
        if hasattr(ort, "preload_dlls"):
            try:
                ort.preload_dlls()
            except Exception:
                pass
    except ImportError as error:
        raise RuntimeError('Install dependencies: python -m pip install -r requirements.txt') from error
    provider = os.environ.get('FACE_STUDIO_PROVIDER', 'CPUExecutionProvider').strip()
    if provider not in {'CPUExecutionProvider', 'CoreMLExecutionProvider', 'CUDAExecutionProvider', 'TensorrtExecutionProvider'} or provider not in ort.get_available_providers():
        raise RuntimeError(f'Unavailable inference provider: {provider}')
    options = ort.SessionOptions()
    cpu_count = os.cpu_count() or 4
    threads = int(os.environ.get('FACE_STUDIO_THREADS', min(cpu_count, 6)))
    options.intra_op_num_threads = max(1, threads)
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.log_severity_level = 3
    providers = [provider] if provider == 'CPUExecutionProvider' else [provider, 'CPUExecutionProvider']
    return ort.InferenceSession(str(path), sess_options=options, providers=providers)


def alignment_matrix(points):
    """Least-squares similarity transform, without random RANSAC alignment."""
    points = np.asarray(points, dtype=np.float64)
    if points.shape != (5, 2) or not np.isfinite(points).all():
        raise RuntimeError('Invalid facial landmarks.')
    target = ARCFACE_POINTS.astype(np.float64)
    source_center, target_center = points.mean(0), target.mean(0)
    source, dest = points - source_center, target - target_center
    variance = np.sum(source ** 2) / len(points)
    if variance < 1e-8:
        raise RuntimeError('Degenerate facial landmarks.')
    u, singular, vt = np.linalg.svd(dest.T @ source / len(points))
    signs = np.ones(2)
    if np.linalg.det(u @ vt) < 0:
        signs[-1] = -1
    rotation = u @ np.diag(signs) @ vt
    scale = np.dot(singular, signs) / variance
    if not np.isfinite(scale) or scale <= 1e-6:
        raise RuntimeError('Degenerate facial landmark scale.')
    matrix = np.empty((2, 3), dtype=np.float32)
    matrix[:, :2] = scale * rotation
    matrix[:, 2] = target_center - scale * rotation @ source_center
    return matrix


class RetinaFace:
    def __init__(self, path):
        self.session = session(path)
        self.input_name = self.session.get_inputs()[0].name
        self.size = 640
        self.priors = self.make_priors(self.size)
        self.threshold = .85
        self._blob = np.zeros((1, 3, self.size, self.size), dtype=np.float32)

    @staticmethod
    def make_priors(size):
        priors = []
        for step, sizes in zip([8, 16, 32], [[16, 32], [64, 128], [256, 512]]):
            for y in range((size + step - 1) // step):
                for x in range((size + step - 1) // step):
                    for minimum in sizes:
                        priors.append([(x + .5) * step / size, (y + .5) * step / size,
                                       minimum / size, minimum / size])
        return np.asarray(priors, dtype=np.float32)

    def setInputSize(self, size):
        # Adapter for the existing service. Inference always uses a 640px square.
        pass

    def detect(self, frame):
        height, width = frame.shape[:2]
        scale = min(self.size / width, self.size / height)
        resized_width, resized_height = round(width * scale), round(height * scale)
        active = cv2.dnn.blobFromImage(
            frame, 1.0, (resized_width, resized_height), (104.0, 117.0, 123.0), swapRB=False
        )
        self._blob[:, :, :resized_height, :resized_width] = active
        if resized_height < self.size:
            self._blob[:, :, resized_height:, :] = 0.0
        if resized_width < self.size:
            self._blob[:, :, :resized_height, resized_width:] = 0.0
        try:
            loc, conf, landmarks = self.session.run(None, {self.input_name: self._blob})
        except Exception as error:
            raise RuntimeError(f'RetinaFace inference failed: {error}') from error
        loc, conf, landmarks = loc[0], conf[0], landmarks[0]
        if loc.shape != (len(self.priors), 4) or landmarks.shape != (len(self.priors), 10):
            raise RuntimeError('Unsupported RetinaFace model outputs; use the bundled R34 checkpoint.')
        selected = np.flatnonzero(conf[:, 1] >= self.threshold)
        selected = selected[np.argsort(conf[selected, 1])[::-1][:5000]]
        if not len(selected):
            return None, None
        prior, offsets = self.priors[selected], loc[selected]
        centers = prior[:, :2] + offsets[:, :2] * .1 * prior[:, 2:]
        sizes = prior[:, 2:] * np.exp(offsets[:, 2:] * .2)
        # Use actual rounded resize ratios to map coordinates back to the frame.
        ratios = np.array([width / resized_width, height / resized_height], np.float32)
        boxes = np.concatenate([centers - sizes / 2, centers + sizes / 2], axis=1) * self.size
        boxes *= np.tile(ratios, 2)
        points = prior[:, None, :2] + landmarks[selected].reshape(-1, 5, 2) * .1 * prior[:, None, 2:]
        points *= self.size * ratios
        boxes[:, 2:] -= boxes[:, :2]
        scores = conf[selected, 1]
        keep = fast_nms(boxes, scores, .4)
        faces = np.concatenate([boxes[keep], points[keep].reshape(-1, 10), scores[keep, None]], axis=1)
        return None, faces.astype(np.float32)


class ArcFace:
    def __init__(self, path):
        self.session = session(path)
        self.input_name = self.session.get_inputs()[0].name

    def alignCrop(self, frame, face):
        matrix = alignment_matrix(face[4:14].reshape(5, 2))
        aligned = cv2.warpAffine(frame, matrix, (112, 112), borderValue=0)
        return normalize_illumination(aligned)

    def feature(self, aligned):
        blob = cv2.dnn.blobFromImage(aligned, 1 / 127.5, (112, 112),
                                     (127.5, 127.5, 127.5), swapRB=True)
        try:
            vector = self.session.run(None, {self.input_name: blob})[0].reshape(-1)
        except Exception as error:
            raise RuntimeError(f'ArcFace inference failed: {error}') from error
        length = np.linalg.norm(vector)
        if vector.shape != (EMBEDDING_SIZE,) or not np.isfinite(vector).all() or length < 1e-8:
            raise RuntimeError('Invalid ArcFace embedding; use the bundled w600k_r50 checkpoint.')
        return vector / length

    def features(self, aligned_crops):
        """Extract normalized embeddings for a batch of 112x112 aligned crops."""
        if not len(aligned_crops):
            return np.empty((0, EMBEDDING_SIZE), dtype=np.float32)
        if len(aligned_crops) == 1:
            return self.feature(aligned_crops[0])[None, :]
        blob = cv2.dnn.blobFromImages(
            list(aligned_crops), 1 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True
        )
        try:
            vectors = self.session.run(None, {self.input_name: blob})[0].reshape(len(aligned_crops), -1)
        except Exception as error:
            raise RuntimeError(f'ArcFace batched inference failed: {error}') from error
        lengths = np.linalg.norm(vectors, axis=1, keepdims=True)
        if vectors.shape != (len(aligned_crops), EMBEDDING_SIZE) or not np.isfinite(vectors).all() or np.any(lengths < 1e-8):
            raise RuntimeError('Invalid ArcFace embeddings; use the bundled w600k_r50 checkpoint.')
        return (vectors / lengths).astype(np.float32)
