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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cv2
import numpy as np

from ble_scanner import BLEScanner, verify_tag
from calibration import load_settings
from face_models import MODEL_ID, ArcFace, RetinaFace
from revision.enrollment_core import THRESHOLD, face_quality, pose_matches, pose_ratios
from revision.photos import jpeg, validate_jpeg
from video_pipeline import (
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
        self.frames = 0
        self.pipeline = VideoPipeline(
            runtime, load_settings(ROOT / "data", dict(DEFAULTS))
        )
        self.outbox = Outbox(runtime / "outbox.sqlite3")
        self.samples = defaultdict(lambda: deque(maxlen=24))
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
        from ble_scanner import BLE_RESULT_TTL, MFG_COMPANY_ID

        for item in self.scanner.latest_results.values():
            if now - item.timestamp > BLE_RESULT_TTL:
                continue
            payload = item.mfr_data.get(MFG_COMPANY_ID)
            label = (
                payload.decode("utf-8", errors="ignore").strip().lower()
                if payload
                else (item.uuids[0] if item.uuids else "")
            )
            if label:
                visible.append(dict(tag=label, rssi=item.rssi))
        self.visible = visible
        return [
            p
            for p in self.gallery
            if p.get("ble_tag")
            and verify_tag(p["ble_tag"], self.scanner.latest_results)
        ]

    def ble_state(self):
        self.tag_list()
        return dict(self.scanner.state(), tags=self.visible)

    def process(self, frame):
        self.initialize()
        _, faces = self.detector.detect(frame)
        faces = [] if faces is None else faces
        now = time.monotonic()
        assign = self.pipeline.associate(faces, now, (frame.shape[1], frame.shape[0]))
        candidates = self.tag_list()
        if not self.current_processing_session():
            return
        # Show detections while the first embedding/decision is being computed.
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
        overlays = []
        for track in list(self.first):
            if track not in self.pipeline.last_seen:
                self.first.pop(track, None)
                self.samples.pop(track, None)
                self.best_captures.pop(track, None)
                self.logged.discard(track)
        for index, face in enumerate(faces):
            track = assign.get(index)
            if track is None:
                continue
            self.first.setdefault(track, now)
            quality = quality_metrics(frame, face)
            warning = rejection(quality, self.settings)
            history = self.samples[track]
            signature = tuple(p["id"] for p in candidates)
            # Candidate changes cannot reuse evidence from a previous BLE identity.
            previous = getattr(self, "candidate_signature", None)
            if previous != signature:
                self.samples.clear()
                self.best_captures.clear()
                history = self.samples[track]
                self.candidate_signature = signature
            if warning is None:
                v = self.recognizer.feature(
                    self.recognizer.alignCrop(frame, face)
                ).flatten()
                if history and float(history[-1]["embedding"] @ v) < 0.3:
                    history.clear()
                    self.best_captures.pop(track, None)
                if not history or now - history[-1]["time"] >= 0.15:
                    history.append(dict(time=now, embedding=v, quality=quality))
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
            people = [
                dict(p, reference=np.asarray(p["reference"], np.float32))
                for p in candidates
            ]
            result = decision(history, people, self.settings, now)
            if history and now - history[-1]["time"] > 1.15:
                result["confirmed"] = False
            fresh = now - self.last_gallery < 30
            outcome = None
            person = candidates[0] if len(candidates) == 1 else None
            if len(faces) > 1 or len(candidates) > 1:
                if now - self.first[track] > 2:
                    outcome = "ambiguous"
            elif len(candidates) == 1 and result["confirmed"] and fresh:
                outcome = "verified"
            elif now - self.first[track] > 4:
                if not fresh:
                    outcome = "service_unavailable"
                elif not candidates:
                    outcome = "missing_ble" if not self.visible else "unknown_tag"
                elif warning:
                    outcome = "timeout"
                else:
                    outcome = "face_mismatch"
            if not self.current_processing_session():
                return
            if outcome and track not in self.logged:
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
            label = (
                person["name"]
                if outcome == "verified"
                else warning
                or ("Verifying" if candidates else "Waiting for assigned BLE tag")
            )
            overlays.append(
                dict(
                    box=face[:4].astype(float).tolist(),
                    label=label,
                    confirmed=outcome == "verified",
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
