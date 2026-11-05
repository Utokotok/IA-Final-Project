"""Native camera/BLE owner; enrollment and backend exchange use loopback APIs."""

import base64
import json
import logging
import os
import secrets
import signal
import sqlite3
import sys
import threading
import time
import uuid
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import cv2
import numpy as np

from services.recognition.ble_scanner import BLEScanner, verify_tag
from services.recognition.calibration import load_settings
from services.recognition.face_models import MODEL_ID, ArcFace, RetinaFace
from services.recognition.enrollment_core import THRESHOLD, face_quality, pose_matches, pose_ratios
from services.recognition.photos import jpeg, validate_jpeg
from services.recognition.video_pipeline import (
    DEFAULTS,
    VideoPipeline,
    decision,
    quality_metrics,
    quality_weight,
    rejection,
)

LOG = logging.getLogger("pnp.monitor")
POSES = ["front", "left", "right", "up", "down"]


def enrollment_result(frame, faces, recognizer, job):
    # The original Face Studio filters detections before counting visible faces.
    faces = [] if faces is None else [face for face in faces if face[14] >= 0.85]
    if len(faces) != 1:
        raise ValueError("Exactly one face must be visible.")
    face = faces[0]
    warning = face_quality(frame, face)
    if warning:
        raise ValueError(warning)
    yaw, pitch = pose_ratios(face)
    if not pose_matches(job["pose"], yaw, pitch, job["baseline"]):
        raise ValueError(
            {
                "front": "Look straight ahead.",
                "left": "Turn gently toward your left shoulder.",
                "right": "Turn gently toward your right shoulder.",
                "up": "Lift your chin slightly.",
                "down": "Lower your chin slightly.",
            }[job["pose"]]
        )
    vector = recognizer.feature(recognizer.alignCrop(frame, face)).flatten()
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(vector).all() or norm < 1e-8:
        raise ValueError("Invalid enrollment embedding. Try again.")
    vector = vector / norm
    references = job.get("references")
    if references is None:
        references = [job["reference"]] if job.get("reference") is not None else []
    if (
        references
        and max(float(np.asarray(v) @ vector) for v in references) < THRESHOLD
    ):
        raise ValueError(
            "Face changed. Return the person who started this registration."
        )
    metrics = quality_metrics(frame, face) or {}
    # Crop with the same 20% margin as the original registration thumbnails.
    x, y, w, h = map(int, face[:4])
    margin = int(max(w, h) * 0.2)
    metrics.update(
        yaw=yaw,
        pitch=pitch,
        crop=[
            max(0, x - margin),
            max(0, y - margin),
            min(frame.shape[1], x + w + margin),
            min(frame.shape[0], y + h + margin),
        ],
    )
    return vector.tolist(), metrics


class Api:
    def __init__(self, token):
        self.token = token

    def call(self, path, data=None, raw=False):
        request = Request(
            "http://127.0.0.1:8000" + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
            },
        )
        with urlopen(request, timeout=5) as response:
            return response.read() if raw else json.load(response)


class Outbox:
    def __init__(self, path):
        self.path = path
        with self.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
        os.chmod(path, 0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, event):
        with self.connection() as db:
            db.execute(
                "INSERT OR IGNORE INTO events VALUES (?,?)",
                (event["id"], json.dumps(event)),
            )

    def flush(self, api):
        with self.connection() as db:
            for ident, payload in db.execute(
                "SELECT id,payload FROM events LIMIT 20"
            ).fetchall():
                api.call("/api/service/events/", json.loads(payload))
                db.execute("DELETE FROM events WHERE id=?", (ident,))

    def count(self):
        with self.connection() as db:
            return db.execute("SELECT COUNT(*) FROM events").fetchone()[0]


class MonitorViewers:
    """Camera lifetime follows dedicated page connections, independently of video."""

    def __init__(self):
        self.lock = threading.Lock()
        self.clients = set()
        self.deadline = 0.0

    def start(self, now=None):
        with self.lock:
            self.deadline = (time.monotonic() if now is None else now) + 5

    def attach(self):
        token = uuid.uuid4().hex
        with self.lock:
            self.clients.add(token)
        return token

    def detach(self, token, now=None):
        with self.lock:
            self.clients.discard(token)
            if not self.clients:
                self.deadline = (time.monotonic() if now is None else now) + 2

    def active(self, now=None):
        with self.lock:
            return (
                bool(self.clients)
                or (time.monotonic() if now is None else now) < self.deadline
            )

    def count(self):
        with self.lock:
            return len(self.clients)


class FrameBuffer:
    """Single latest frame: preview never waits for inference or backend requests."""

    def __init__(self):
        self.condition = threading.Condition()
        self.frame = None
        self.jpeg = None
        self.sequence = 0
        self.generation = 0
        self.running = False
        self.timestamps = deque(maxlen=90)

    def start(self):
        with self.condition:
            self.generation += 1
            self.running = True
            self.frame = self.jpeg = None
            self.timestamps.clear()
            self.condition.notify_all()

    def stop(self):
        with self.condition:
            self.running = False
            self.frame = self.jpeg = None
            self.timestamps.clear()
            self.condition.notify_all()

    def publish(self, frame, jpeg):
        with self.condition:
            self.frame = frame
            self.jpeg = jpeg
            self.sequence += 1
            self.timestamps.append(time.monotonic())
            self.condition.notify_all()

    def latest(self):
        with self.condition:
            return self.frame, self.sequence, self.generation

    def next_jpeg(self, sequence, timeout=1):
        with self.condition:
            self.condition.wait_for(
                lambda: self.sequence != sequence or not self.running,
                timeout=timeout,
            )
            return self.jpeg, self.sequence, self.running

    def fps(self):
        with self.condition:
            if len(self.timestamps) < 2:
                return 0.0
            elapsed = self.timestamps[-1] - self.timestamps[0]
            return (
                round((len(self.timestamps) - 1) / elapsed, 1) if elapsed > 0 else 0.0
            )


def evaluate_hybrid_gate(face, frame_shape, trajectory, settings, now):
    """Evaluate the 3-way walk-through hybrid gate: kinematics + optical scale."""
    x, y, w, h = map(float, face[:4])
    frame_h, frame_w = float(frame_shape[0]), float(frame_shape[1])
    face_size = min(w, h)
    cx_norm = (x + w * 0.5) / max(frame_w, 1.0)
    cy_norm = (y + h * 0.5) / max(frame_h, 1.0)

    if not trajectory or now - trajectory[-1]["time"] >= 0.04:
        trajectory.append(dict(time=now, cx=cx_norm, cy=cy_norm, size=face_size))
    while len(trajectory) > 1 and now - trajectory[0]["time"] > 1.2:
        trajectory.popleft()

    lateral_velocity = 0.0
    vertical_velocity = 0.0
    scale_velocity = 0.0
    if len(trajectory) >= 2:
        anchor = trajectory[0]
        dt = now - anchor["time"]
        if dt >= 0.08:
            lateral_velocity = abs(cx_norm - anchor["cx"]) / dt
            vertical_velocity = (cy_norm - anchor["cy"]) / dt
            scale_velocity = (face_size - anchor["size"]) / dt

    in_corridor = 0.06 <= cx_norm <= 0.94
    approaching_camera = scale_velocity >= 12.0 or (
        scale_velocity >= 4.0 and vertical_velocity > 0.15
    )
    is_lateral_passerby = lateral_velocity > 0.45 and not approaching_camera
    is_receding = scale_velocity < -25.0
    kinematics_ready = in_corridor and not is_lateral_passerby and not is_receding

    min_trigger_size = float(settings.get("min_size", 100.0))
    scale_ready = face_size >= min_trigger_size
    distance_m = round(
        max(0.4, min(4.5, (min_trigger_size * 1.2) / max(face_size, 25.0))), 1
    )

    return dict(
        face_size=round(face_size, 1),
        distance_m=distance_m,
        scale_ready=scale_ready,
        kinematics_ready=kinematics_ready,
        is_lateral_passerby=is_lateral_passerby,
        is_receding=is_receding,
        lateral_velocity=round(lateral_velocity, 3),
        vertical_velocity=round(vertical_velocity, 3),
        scale_velocity=round(scale_velocity, 1),
        triggered=bool(scale_ready and kinematics_ready),
    )


def evaluate_verification_outcome(
    *,
    is_triggered,
    competing_faces,
    candidates,
    visible_tags,
    result,
    warning,
    fresh,
    elapsed,
    threshold,
):
    """Pure state machine transition for access verification decisions."""
    if not is_triggered:
        return None
    if competing_faces > 1 or len(candidates) > 1:
        return "ambiguous" if elapsed > 2 else None
    if len(candidates) == 1 and result["confirmed"] and fresh:
        return "verified"
    if elapsed <= 4:
        return None
    if not fresh:
        return "service_unavailable"
    if not candidates:
        return "missing_ble" if not visible_tags else "unknown_tag"
    similarity = result.get("similarity")
    if warning or (similarity is not None and similarity >= threshold):
        return "timeout"
    return "face_mismatch"


def resolve_overlay_stage(outcome, person, is_triggered, gate, warning, candidates):
    """Determine UI stage and human-readable status label for a tracked face."""
    default_prompt = "Verifying" if candidates else "Waiting for assigned BLE tag"
    if outcome == "verified" and person:
        return "verified", person["name"]
    if outcome:
        return "denied", warning or default_prompt
    if is_triggered:
        return "evaluating", warning or default_prompt
    if not gate["kinematics_ready"]:
        label = (
            "Passerby ignored"
            if gate["is_lateral_passerby"]
            else "Outside approach zone"
        )
        return "bystander", label
    return "approaching", f"Approaching · {gate['distance_m']:.1f}m"


class Monitor:
    def __init__(self, runtime, token):
        self.runtime = runtime
        self.token = token
        self.api = Api(token)
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.requested = False
        self.viewers = MonitorViewers()
        self.camera_note = ""
        self.capture = None
        self.camera_session = ""
        self.capture_session = ""
        self.recognition_status = "loading"
        self.buffer = FrameBuffer()
        self.overlays = []
        self.overlay_size = [1280, 720]
        self.overlay_time = 0
        self.inference_times = deque(maxlen=30)
        self.error = None
        self.scanner = BLEScanner()
        self.scanner.start()
        self.detector = None
        self.recognizer = None
        self.gallery = []
        self.gallery_version = ""
        self.last_gallery = 0
        self.backend_ok = False
        self.direction = "entry"
        self.latest = None
        self.latest_images = {}
        self.best_captures = {}
        self.photo_cache = runtime / "reference_photos"
        self.photo_cache.mkdir(mode=0o700, exist_ok=True)
        self.visible = []
        self.active_candidates = []
        self.frames = 0
        self.pipeline = VideoPipeline(
            runtime, load_settings(ROOT / "data", dict(DEFAULTS))
        )
        self.outbox = Outbox(runtime / "outbox.sqlite3")
        self.samples = defaultdict(lambda: deque(maxlen=24))
        self.trajectories = defaultdict(lambda: deque(maxlen=16))
        self.first = {}
        self.logged = set()
        self.session = uuid.uuid4().hex
        self.settings = self.pipeline.settings
        self.load_cache()

    def load_cache(self):
        path = self.runtime / "gallery.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            if data["model_id"] != MODEL_ID:
                return
            for p in data["people"]:
                v = np.asarray(p["reference"], np.float32)
                if (
                    v.shape != (512,)
                    or not np.isfinite(v).all()
                    or abs(np.linalg.norm(v) - 1) > 0.01
                ):
                    return
            self.gallery = data["people"]
            self.gallery_version = data["version"]
        except (ValueError, KeyError, OSError):
            LOG.exception("Invalid local gallery cache")

    def start_camera(self):
        self.viewers.start()
        self.camera_note = ""
        self.error = ""
        if not self.requested:
            self.camera_session = uuid.uuid4().hex
        self.requested = True

    def cache_photos(self, people):
        for person in people:
            if not person.get("photo_available"):
                continue
            version = uuid.UUID(person["profile_version"])
            path = self.photo_cache / (str(version) + ".jpg")
            if path.exists():
                continue
            try:
                raw = self.api.call(
                    f"/api/service/people/{person['id']}/photo/{version}/", raw=True
                )
                validate_jpeg(raw)
                temp = path.with_suffix(".tmp")
                with temp.open("wb") as output:
                    os.chmod(temp, 0o600)
                    output.write(raw)
                temp.replace(path)
            except (OSError, ValueError):
                LOG.exception("Registration photo could not be cached")

    def registered_photo(self, person):
        if not person or not person.get("photo_available"):
            return None
        try:
            path = self.photo_cache / (
                str(uuid.UUID(person["profile_version"])) + ".jpg"
            )
            return validate_jpeg(path.read_bytes())
        except (OSError, ValueError):
            return None

    def initialize(self):
        if self.detector is not None and self.recognizer is not None:
            return
        self.recognition_status = "loading"
        try:
            detector = RetinaFace(ROOT / "models/retinaface_r34.onnx")
            detector.threshold = 0.65
            recognizer = ArcFace(ROOT / "models/w600k_r50.onnx")
            # Pay model loading and first-inference costs before live verification.
            detector.detect(np.zeros((720, 1280, 3), np.uint8))
            recognizer.feature(np.zeros((112, 112, 3), np.uint8))
            cache = self.runtime / "font_cache"
            cache.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.environ.setdefault("MPLCONFIGDIR", str(cache))
            # ByteTrack lazily imports supervision/matplotlib on its first use.
            # Initialize it before advertising that recognition is ready.
            self.pipeline.associate([], time.monotonic(), (1280, 720))
            self.pipeline.reset()
            self.detector, self.recognizer = detector, recognizer
            self.recognition_status = "ready"
        except Exception:
            self.recognition_status = "error"
            raise

    def current_processing_session(self):
        session = getattr(self, "processing_session", None)
        return session is None or (
            self.requested and session == self.camera_session == self.capture_session
        )

    def state(self):
        with self.lock:
            return dict(
                running=self.requested
                and self.capture is not None
                and self.capture_session == self.camera_session,
                requested=self.requested,
                camera_session=self.camera_session,
                recognition_status=self.recognition_status,
                monitor_views=self.viewers.count(),
                camera_note=self.camera_note,
                error=self.error,
                backend_ok=self.backend_ok,
                gallery_count=len(self.gallery),
                gallery_version=self.gallery_version,
                gallery_fresh=time.monotonic() - self.last_gallery < 30,
                direction=self.direction,
                latest=self.latest,
                tags=self.visible,
                candidates=getattr(self, "active_candidates", []),
                queued=self.outbox.count(),
                frames=self.frames,
                preview_fps=self.buffer.fps(),
                recognition_fps=self.recognition_fps(),
                overlay_size=self.overlay_size,
                overlays=self.overlays
                if time.monotonic() - self.overlay_time < 1.15
                else [],
                ble_enabled=self.scanner.enabled,
                ble_last_scan=self.scanner.last_scan_time,
                ble_error=self.scanner.last_error,
            )

    def recognition_fps(self):
        if len(self.inference_times) < 2:
            return 0.0
        elapsed = self.inference_times[-1] - self.inference_times[0]
        return (
            round((len(self.inference_times) - 1) / elapsed, 1) if elapsed > 0 else 0.0
        )

    def sync(self):
        self.tag_list()
        data = self.api.call("/api/service/gallery/")
        if data["model_id"] != MODEL_ID:
            raise ValueError("Incompatible gallery model")
        for p in data["people"]:
            v = np.asarray(p["reference"], np.float32)
            if (
                v.shape != (512,)
                or not np.isfinite(v).all()
                or abs(np.linalg.norm(v) - 1) > 0.01
            ):
                raise ValueError("Invalid gallery embedding")
        if data["version"] != self.gallery_version:
            self.gallery = data["people"]
            self.gallery_version = data["version"]
            self.samples.clear()
            self.trajectories.clear()
            self.best_captures.clear()
            self.first.clear()
            self.logged.clear()
            self.pipeline.reset()
            temp = self.runtime / "gallery.tmp"
            temp.write_text(json.dumps(data))
            temp.chmod(0o600)
            temp.replace(self.runtime / "gallery.json")
        self.cache_photos(data["people"])
        self.last_gallery = time.monotonic()
        result = self.api.call(
            "/api/service/heartbeat/",
            dict(
                gallery_version=self.gallery_version,
                health=dict(
                    running=self.capture is not None,
                    error=self.error,
                    ble_enabled=self.scanner.enabled,
                    ble_scanning=self.scanner.scanning,
                    ble_error=self.scanner.last_error,
                    tags=self.visible,
                    queued=self.outbox.count(),
                    frames=self.frames,
                ),
            ),
        )
        new_direction = result.get("direction", "entry")
        if new_direction != self.direction:
            self.samples.clear()
            self.trajectories.clear()
            self.best_captures.clear()
            self.first.clear()
            self.logged.clear()
            self.pipeline.reset()
        self.direction = new_direction
        self.outbox.flush(self.api)
        self.backend_ok = True

    def tag_list(self):
        now = time.monotonic()
        visible = []
        from services.recognition.ble_scanner import BLE_RESULT_TTL, MFG_COMPANY_ID

        for item in self.scanner.latest_results.values():
            if now - item.timestamp > BLE_RESULT_TTL:
                continue
            payload = item.mfr_data.get(MFG_COMPANY_ID)
            label = ""
            if payload:
                # Prune angle brackets (<, >) and non-printable control characters
                decoded = "".join(
                    c for c in payload.decode("utf-8", errors="ignore")
                    if 32 <= ord(c) < 127 and c not in "<>"
                ).strip().lower()
                if decoded and any(c.isalnum() for c in decoded):
                    label = decoded
            elif item.uuids:
                u = item.uuids[0].strip().lower()
                cleaned_u = "".join(c for c in u if c in "0123456789abcdef-")
                if cleaned_u:
                    label = cleaned_u
            if label:
                visible.append(dict(tag=label, rssi=item.rssi))
        self.visible = visible
        candidates = [
            p
            for p in self.gallery
            if p.get("ble_tag")
            and verify_tag(p["ble_tag"], self.scanner.latest_results)
        ]
        self.active_candidates = [
            dict(id=p["id"], name=p["name"], ble_tag=p.get("ble_tag"))
            for p in candidates
        ]
        return candidates

    def ble_state(self):
        self.tag_list()
        return dict(self.scanner.state(), tags=self.visible)

    def _prune_stale_tracks(self, trajectories):
        for track in list(self.first) + list(trajectories):
            if track not in self.pipeline.last_seen:
                self.first.pop(track, None)
                self.samples.pop(track, None)
                trajectories.pop(track, None)
                self.best_captures.pop(track, None)
                self.logged.discard(track)

    def _sync_candidate_signature(self, candidates):
        signature = tuple(sorted(p["id"] for p in candidates))
        previous = getattr(self, "candidate_signature", None)
        if previous is not None and signature and previous != signature:
            if not set(previous).intersection(set(signature)):
                self.samples.clear()
                self.best_captures.clear()
        if signature:
            self.candidate_signature = signature

    def _record_sample(self, track, frame, face, embedding, quality, now):
        history = self.samples[track]
        if history and float(history[-1]["embedding"] @ embedding) < 0.3:
            history.clear()
            self.best_captures.pop(track, None)
        if not history or now - history[-1]["time"] >= 0.15:
            history.append(dict(time=now, embedding=embedding, quality=quality))
            current = self.best_captures.get(track)
            weight = quality_weight(quality)
            if (
                current is None
                or now - current["time"] > 6
                or weight > current["weight"]
            ):
                self.best_captures[track] = dict(
                    time=now,
                    weight=weight,
                    image=jpeg(frame, face),
                    stamp=datetime.now(timezone.utc).isoformat(),
                )

    def _record_event(self, track, frame, face, outcome, person, result, now):
        best = self.best_captures.get(track)
        if best and now - best["time"] <= 6:
            captured, captured_at = best["image"], best["stamp"]
        else:
            captured, captured_at = (
                jpeg(frame, face),
                datetime.now(timezone.utc).isoformat(),
            )
        registered = self.registered_photo(person)
        event = dict(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat(),
            captured_image=base64.b64encode(captured).decode("ascii"),
            registered_image=base64.b64encode(registered).decode("ascii")
            if registered
            else None,
            captured_at=captured_at,
            registered_at=person.get("registered_at")
            if person and registered
            else None,
            direction=self.direction,
            outcome=outcome,
            person_id=person["id"] if person else None,
            snapshot={
                key: (person.get(key) or "")
                for key in ["name", "personnel_id", "rank", "unit", "ble_tag"]
            }
            if person
            else {},
            similarity=result.get("similarity"),
            evidence=dict(
                gallery_version=self.gallery_version,
                model_id=MODEL_ID,
                track_id=track,
                session=self.session,
                ble_tags=self.visible,
                ble_verified=person is not None,
                profile_version=person.get("profile_version")
                if person
                else None,
                settings=self.settings,
            ),
        )
        self.outbox.add(event)
        self.logged.add(track)
        with self.lock:
            self.latest_images = dict(
                id=event["id"], captured=captured, registered=registered
            )
        self.latest = dict(
            {
                k: v
                for k, v in event.items()
                if k not in ["captured_image", "registered_image"]
            },
            captured_image_url=f"/api/monitor/images/captured/?event_id={event['id']}",
            registered_image_url=f"/api/monitor/images/registered/?event_id={event['id']}"
            if registered
            else None,
            name=person["name"] if person else "Unidentified",
            personnel_id=person["personnel_id"] if person else "",
            unit=person.get("unit", "") if person else "",
            ble_tag=person.get("ble_tag") if person else None,
        )

    def process(self, frame):
        self.initialize()
        _, faces = self.detector.detect(frame)
        faces = [] if faces is None else faces
        now = time.monotonic()
        assign = self.pipeline.associate(faces, now, (frame.shape[1], frame.shape[0]))
        candidates = self.tag_list()
        if not self.current_processing_session():
            return
        with self.lock:
            self.overlays = [
                dict(
                    box=face[:4].astype(float).tolist(),
                    label="Verifying" if candidates else "Waiting for assigned BLE tag",
                    confirmed=False,
                )
                for face in faces
            ]
            self.overlay_size = [frame.shape[1], frame.shape[0]]
            self.overlay_time = time.monotonic()

        trajectories = getattr(self, "trajectories", None)
        if trajectories is None:
            trajectories = self.trajectories = defaultdict(lambda: deque(maxlen=16))
        self._prune_stale_tracks(trajectories)
        self._sync_candidate_signature(candidates)

        # Evaluate approach corridor kinematics and quality for all tracked faces
        tracked_items = []
        for index, face in enumerate(faces):
            track = assign.get(index)
            if track is None:
                continue
            gate = evaluate_hybrid_gate(
                face, frame.shape, trajectories[track], self.settings, now
            )
            if gate["triggered"]:
                self.first.setdefault(track, now)
            else:
                self.first.pop(track, None)
            quality = quality_metrics(frame, face)
            warning = rejection(quality, self.settings)
            tracked_items.append((index, track, face, gate, quality, warning))

        # Only faces triggered inside the active approach corridor compete for passage
        competing_faces = sum(1 for item in tracked_items if item[3]["triggered"])

        # Batch ArcFace feature extraction when multiple valid faces are ready
        ready_indices = [
            i for i, item in enumerate(tracked_items)
            if item[5] is None and item[3]["kinematics_ready"]
        ]
        extracted_vectors = {}
        if len(ready_indices) > 1 and isinstance(self.recognizer, ArcFace):
            crops = [
                self.recognizer.alignCrop(frame, tracked_items[i][2])
                for i in ready_indices
            ]
            batch_vecs = self.recognizer.features(crops)
            for i, vec in zip(ready_indices, batch_vecs):
                extracted_vectors[i] = vec.flatten()
        else:
            for i in ready_indices:
                face = tracked_items[i][2]
                extracted_vectors[i] = self.recognizer.feature(
                    self.recognizer.alignCrop(frame, face)
                ).flatten()

        people = [
            dict(p, reference=np.asarray(p["reference"], np.float32))
            for p in candidates
        ]
        full_gallery = getattr(self, "gallery", None)
        fresh = now - self.last_gallery < 30
        person = candidates[0] if len(candidates) == 1 else None
        overlays = []

        for list_idx, (_, track, face, gate, quality, warning) in enumerate(tracked_items):
            is_triggered = gate["triggered"]
            if list_idx in extracted_vectors:
                self._record_sample(
                    track, frame, face, extracted_vectors[list_idx], quality, now
                )
            history = self.samples[track]
            result = decision(history, people, self.settings, now, gallery=full_gallery)
            if history and now - history[-1]["time"] > 1.15:
                result["confirmed"] = False

            elapsed = (now - self.first[track]) if is_triggered else 0.0
            outcome = evaluate_verification_outcome(
                is_triggered=is_triggered,
                competing_faces=competing_faces,
                candidates=candidates,
                visible_tags=self.visible,
                result=result,
                warning=warning,
                fresh=fresh,
                elapsed=elapsed,
                threshold=self.settings["threshold"],
            )
            if not self.current_processing_session():
                return
            if outcome and track not in self.logged:
                self._record_event(track, frame, face, outcome, person, result, now)

            stage, label = resolve_overlay_stage(
                outcome, person, is_triggered, gate, warning, candidates
            )
            overlays.append(
                dict(
                    box=face[:4].astype(float).tolist(),
                    label=label,
                    confirmed=outcome == "verified",
                    triggered=is_triggered,
                    stage=stage,
                    distance_m=gate["distance_m"],
                    scale_ready=gate["scale_ready"],
                    kinematics_ready=gate["kinematics_ready"],
                )
            )

        if not self.current_processing_session():
            return
        with self.lock:
            self.overlays = overlays
            self.overlay_size = [frame.shape[1], frame.shape[0]]
            self.overlay_time = time.monotonic()
            self.frames += 1

    def enrollment(self):
        job = self.api.call("/api/service/jobs/claim/", {}).get("job")
        if not job:
            return
        payload = {"lease": job["lease"]}
        try:
            self.initialize()
            raw = self.api.call(job["image_url"], raw=True)
            frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                raise ValueError("Invalid JPEG.")
            _, faces = self.detector.detect(frame)
            features, quality = enrollment_result(frame, faces, self.recognizer, job)
            payload.update(embedding=features, quality=quality)
        except Exception as exc:
            payload["error"] = str(exc)[:400]
        self.api.call(f"/api/service/jobs/{job['id']}/complete/", payload)

    def capture_loop(self):
        """The only camera owner; continuously publish fresh frames for preview."""
        while not self.stopping.is_set():
            # Opening a macOS webcam can outlast the preview connection grace.
            # Start the disconnect deadline only after a frame can be delivered.
            if (
                self.requested
                and self.buffer.jpeg is not None
                and not self.viewers.active()
            ):
                self.requested = False
                self.camera_note = (
                    "Camera stopped because the last monitoring page disconnected."
                )
                LOG.info("Camera stopped: no active monitoring pages")
            if self.capture is not None and (
                not self.requested or self.capture_session != self.camera_session
            ):
                self.capture.release()
                self.capture = None
                self.capture_session = ""
                self.buffer.stop()
                with self.lock:
                    self.overlays = []
                    self.inference_times.clear()
            if not self.requested:
                self.stopping.wait(0.05)
                continue
            try:
                if self.capture is None:
                    session = self.camera_session
                    cam = cv2.VideoCapture(0)
                    if not cam.isOpened():
                        cam.release()
                        raise RuntimeError(
                            "Cannot open webcam. Allow camera access for Python in macOS Settings."
                        )
                    cam.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                    cam.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                    cam.set(cv2.CAP_PROP_FPS, 30)
                    if not self.requested or session != self.camera_session:
                        cam.release()
                        continue
                    self.capture = cam
                    self.capture_session = session
                    self.buffer.start()
                ok, frame = self.capture.read()
                if not self.requested or self.capture_session != self.camera_session:
                    continue
                if not ok:
                    raise RuntimeError("The camera did not return a frame.")
                if max(frame.shape[:2]) > 1280:
                    scale = 1280 / max(frame.shape[:2])
                    frame = cv2.resize(
                        frame,
                        (round(frame.shape[1] * scale), round(frame.shape[0] * scale)),
                    )
                ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if ok:
                    if self.buffer.jpeg is None:
                        self.viewers.start()
                    self.buffer.publish(frame, jpeg.tobytes())
            except Exception as exc:
                self.error = str(exc)[:400]
                self.requested = False
                LOG.exception("Camera capture error")
                self.stopping.wait(0.1)
        if self.capture is not None:
            self.capture.release()
            self.capture = None
        self.buffer.stop()

    def run(self):
        synced = 0
        enrolled = 0
        processed = -1
        generation = -1
        while not self.stopping.is_set():
            try:
                self.initialize()
            except Exception as exc:
                self.error = str(exc)[:400]
                LOG.exception("Face model initialization failed")
                self.stopping.wait(1)
                continue
            now = time.monotonic()
            if now - synced > 2:
                try:
                    self.sync()
                except Exception:
                    self.backend_ok = False
                    LOG.warning(
                        "Backend unavailable; using cached gallery and event outbox."
                    )
                synced = now
            try:
                frame, sequence, current_generation = self.buffer.latest()
                if self.requested and frame is not None and sequence != processed:
                    if generation != current_generation:
                        self.pipeline.reset()
                        self.first.clear()
                        self.samples.clear()
                        self.best_captures.clear()
                        self.logged.clear()
                        generation = current_generation
                    # No frame queue: recognition always consumes the newest capture.
                    processed = sequence
                    self.processing_session = self.capture_session
                    self.process(frame)
                    with self.lock:
                        self.inference_times.append(time.monotonic())
                    self.error = None
                if self.backend_ok and now - enrolled > 0.2:
                    self.enrollment()
                    enrolled = now
            except Exception as exc:
                self.error = str(exc)[:400]
                LOG.exception("Recognition processing error")
                self.stopping.wait(0.2)
            self.stopping.wait(0.005 if self.requested else 0.1)


class Handler(BaseHTTPRequestHandler):
    def authorized(self):
        if self.headers.get("Host") not in ["127.0.0.1:8766", "localhost:8766"]:
            return False
        return secrets.compare_digest(
            self.headers.get("Authorization", ""), "Bearer " + self.server.monitor.token
        )

    def log_message(self, *args):
        pass

    def do_GET(self):
        if not self.authorized():
            self.send_error(403)
            return
        monitor = self.server.monitor
        if self.path == "/presence":
            self.presence(monitor)
            return
        if urlsplit(self.path).path == "/stream":
            self.stream(monitor)
            return
        if self.path.startswith("/latest/"):
            parts = self.path.split("/")
            if len(parts) != 4 or parts[2] not in ["captured", "registered"]:
                self.send_error(404)
                return
            with monitor.lock:
                photos = monitor.latest_images
                body = photos.get(parts[2]) if photos.get("id") == parts[3] else None
            if not body:
                self.send_error(404)
                return
            kind = "image/jpeg"
        elif self.path == "/frame":
            with monitor.buffer.condition:
                body = monitor.buffer.jpeg
            if body is None:
                self.send_error(503)
                return
            kind = "image/jpeg"
        elif self.path == "/ble":
            body = json.dumps(monitor.ble_state()).encode()
            kind = "application/json"
        elif self.path == "/status":
            body = json.dumps(monitor.state()).encode()
            kind = "application/json"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def presence(self, monitor):
        viewer = monitor.viewers.attach()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"data: connected\nretry: 500\n\n")
            self.wfile.flush()
            while not monitor.stopping.wait(0.5):
                self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            monitor.viewers.detach(viewer)
        self.close_connection = True

    def stream(self, monitor):
        session = parse_qs(urlsplit(self.path).query).get(
            "session", [monitor.camera_session]
        )[0]
        if not monitor.requested or session != monitor.camera_session:
            self.send_error(503)
            return
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        sequence = -1
        try:
            while (
                not monitor.stopping.is_set()
                and monitor.requested
                and session == monitor.camera_session
            ):
                # Allow video requests during webcam warmup too.
                if not monitor.buffer.running or monitor.capture_session != session:
                    monitor.stopping.wait(0.05)
                    continue
                jpeg, current, running = monitor.buffer.next_jpeg(sequence)
                if not running:
                    break
                if (
                    session != monitor.camera_session
                    or monitor.capture_session != session
                ):
                    break
                if jpeg is None or current == sequence:
                    continue
                sequence = current
                self.wfile.write(
                    b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + str(len(jpeg)).encode()
                    + b"\r\n\r\n"
                    + jpeg
                    + b"\r\n"
                )
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        self.close_connection = True

    def do_POST(self):
        if not self.authorized():
            self.send_error(403)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size > 1000:
                raise ValueError()
            data = json.loads(self.rfile.read(size))
            if data.get("action") == "start":
                self.server.monitor.start_camera()
            elif data.get("action") == "stop":
                self.server.monitor.requested = False
            if self.path == "/ble":
                self.server.monitor.scanner.request_scan()
            else:
                self.path = "/status"
            self.do_GET()
        except (ValueError, TypeError):
            self.send_error(400)


def main():
    logging.basicConfig(level=logging.INFO)
    runtime = ROOT / "runtime"
    config = json.loads((runtime / "config.json").read_text())
    monitor = Monitor(runtime, config["service_token"])
    server = ThreadingHTTPServer(("127.0.0.1", 8766), Handler)
    server.monitor = monitor
    thread = threading.Thread(target=monitor.run, daemon=True)
    thread.start()
    capture_thread = threading.Thread(target=monitor.capture_loop, daemon=True)
    capture_thread.start()

    def terminate(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stopping.set()
        monitor.requested = False
        monitor.scanner.stop()
        monitor.buffer.stop()
        capture_thread.join(timeout=5)
        thread.join(timeout=5)
        server.server_close()


if __name__ == "__main__":
    main()
