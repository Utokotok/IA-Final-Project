import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import numpy as np

from services.recognition.worker import Outbox, enrollment_result, pose_matches


class WorkerTests(unittest.TestCase):
    def test_outbox_survives_restart_and_failed_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outbox.sqlite3"
            o = Outbox(path)
            event = {"id": "id", "outcome": "verified"}
            o.add(event)
            o.add(event)
            self.assertEqual(Outbox(path).count(), 1)
            api = Mock()
            api.call.side_effect = OSError("backend down")
            with self.assertRaises(OSError):
                o.flush(api)
            self.assertEqual(o.count(), 1)
            api.call.side_effect = None
            o.flush(api)
            self.assertEqual(o.count(), 0)

    def test_enrollment_rejects_multiple_faces(self):
        with self.assertRaisesRegex(ValueError, "Exactly one"):
            enrollment_result(
                np.zeros((400, 400, 3), np.uint8), np.ones((2, 15)), None, {}
            )

    def test_pose_guidance(self):
        self.assertTrue(pose_matches("front", 0, 0.5, [0, 0.5]))
        self.assertFalse(pose_matches("left", 0, 0.5, [0, 0.5]))
        self.assertTrue(pose_matches("left", 0.2, 0.5, [0, 0.5]))
        self.assertTrue(pose_matches("up", 0, 0.4, [0, 0.5]))


if __name__ == "__main__":
    unittest.main()


class VerificationDecisionTests(unittest.TestCase):
    def make_monitor(self, directory, candidates):
        import threading
        from collections import defaultdict, deque

        from services.recognition.worker import Monitor
        from services.recognition.video_pipeline import DEFAULTS

        m = Monitor.__new__(Monitor)
        m.detector = Mock()
        m.recognizer = Mock()
        m.recognizer.feature.return_value = np.array(
            [1.0] + [0.0] * 511, dtype=np.float32
        )
        face = np.zeros(15, np.float32)
        face[:4] = [40, 40, 150, 150]
        face[14] = 0.99
        m.detector.detect.return_value = (None, np.array([face]))
        m.pipeline = Mock()
        m.pipeline.associate.return_value = {0: 1}
        m.pipeline.last_seen = {1: 10}
        m.samples = defaultdict(lambda: deque(maxlen=24))
        m.best_captures = {}
        m.latest_images = {}
        m.first = {1: 10}
        m.logged = set()
        m.tag_list = Mock(return_value=candidates)
        m.settings = dict(DEFAULTS)
        m.last_gallery = 10
        m.gallery_version = "gallery"
        m.visible = []
        m.session = "session"
        m.direction = "entry"
        m.latest = None
        m.lock = threading.Lock()
        m.frames = 0
        m.outbox = Outbox(Path(directory) / "outbox.sqlite3")
        return m

    def exercise(self, m, times):
        from unittest.mock import patch

        metrics = dict(
            sharpness=100,
            size=150,
            yaw=0,
            brightness=100,
            confidence=0.99,
            overexposed=0,
        )
        with (
            patch("services.recognition.worker.quality_metrics", return_value=metrics),
            patch(
                "services.recognition.worker.time.monotonic",
                side_effect=[t for t in times for _ in range(3)],
            ),
        ):
            for _ in times:
                m.process(np.zeros((240, 320, 3), np.uint8))

    def test_matching_face_and_ble_require_multiple_samples(self):
        with tempfile.TemporaryDirectory() as d:
            p = dict(
                id="person",
                name="Officer",
                personnel_id="P01",
                ble_tag="tag",
                reference=[1.0] + [0.0] * 511,
            )
            m = self.make_monitor(d, [p])
            self.exercise(m, [10, 10.2, 10.4])
            self.assertEqual(m.latest["outcome"], "verified")
            self.assertEqual(m.outbox.count(), 1)

    def test_matching_face_above_threshold_times_out_not_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            p = dict(
                id="person",
                name="Officer",
                personnel_id="P01",
                ble_tag="tag",
                reference=[1.0] + [0.0] * 511,
            )
            m = self.make_monitor(d, [p])
            # Only 1 sample given at t=10; at t=15 (>4s) verification checks did not reach 3 samples
            self.exercise(m, [10, 15])
            self.assertEqual(m.latest["outcome"], "timeout")
            self.assertGreaterEqual(m.latest["similarity"], 0.60)

    def test_unmatching_face_results_in_face_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            p = dict(
                id="person",
                name="Officer",
                personnel_id="P01",
                ble_tag="tag",
                reference=[1.0] + [0.0] * 511,
            )
            m = self.make_monitor(d, [p])
            # Recognize face orthogonal to registered officer
            m.recognizer.feature.return_value = np.array([0.0, 1.0] + [0.0] * 510, dtype=np.float32)
            self.exercise(m, [10, 15])
            self.assertEqual(m.latest["outcome"], "face_mismatch")
            self.assertLess(m.latest["similarity"], 0.60)

    def test_transient_ble_drop_does_not_purge_accumulated_face_samples(self):
        with tempfile.TemporaryDirectory() as d:
            p = dict(
                id="person",
                name="Officer",
                personnel_id="P01",
                ble_tag="tag",
                reference=[1.0] + [0.0] * 511,
            )
            m = self.make_monitor(d, [p])
            # Sample 1 with tag present
            self.exercise(m, [10])
            # Transient drop: candidate list is empty
            m.tag_list = Mock(return_value=[])
            self.exercise(m, [10.2])
            # Tag returns; samples 2 and 3 arrive
            m.tag_list = Mock(return_value=[p])
            self.exercise(m, [10.4, 10.6])
            self.assertEqual(m.latest["outcome"], "verified")

    def test_face_only_never_grants(self):
        with tempfile.TemporaryDirectory() as d:
            m = self.make_monitor(d, [])
            self.exercise(m, [15])
            self.assertEqual(m.latest["outcome"], "missing_ble")

    def test_stale_gallery_never_grants(self):
        with tempfile.TemporaryDirectory() as d:
            p = dict(
                id="person",
                name="Officer",
                personnel_id="P01",
                ble_tag="tag",
                reference=[1.0] + [0.0] * 511,
            )
            m = self.make_monitor(d, [p])
            m.last_gallery = -100
            self.exercise(m, [15, 15.2, 15.4])
            self.assertEqual(m.latest["outcome"], "service_unavailable")

    def test_multiple_beacons_are_ambiguous(self):
        with tempfile.TemporaryDirectory() as d:
            p = dict(
                id="person",
                name="Officer",
                personnel_id="P01",
                ble_tag="tag",
                reference=[1.0] + [0.0] * 511,
            )
            m = self.make_monitor(d, [p, dict(p, id="other")])
            self.exercise(m, [13])
            self.assertEqual(m.latest["outcome"], "ambiguous")

    def test_distant_bystander_does_not_block_active_entrant(self):
        with tempfile.TemporaryDirectory() as d:
            p = dict(
                id="person",
                name="Officer",
                personnel_id="P01",
                ble_tag="tag",
                reference=[1.0] + [0.0] * 511,
            )
            m = self.make_monitor(d, [p])
            active_face = np.zeros(15, np.float32)
            active_face[:4] = [80, 40, 150, 150]
            active_face[14] = 0.99
            bystander_face = np.zeros(15, np.float32)
            bystander_face[:4] = [10, 60, 65, 65]
            bystander_face[14] = 0.99
            m.detector.detect.return_value = (None, np.array([active_face, bystander_face]))
            m.pipeline.associate.return_value = {0: 1, 1: 2}
            m.pipeline.last_seen = {1: 10, 2: 10}
            self.exercise(m, [10, 10.2, 12.5])
            self.assertEqual(m.latest["outcome"], "verified")

    def test_distant_face_stays_in_approaching_state_without_premature_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            m = self.make_monitor(d, [])
            distant_face = np.zeros(15, np.float32)
            distant_face[:4] = [120, 60, 65, 65]
            distant_face[14] = 0.99
            m.detector.detect.return_value = (None, np.array([distant_face]))
            self.exercise(m, [10, 12, 16])
            self.assertIsNone(m.latest)
            self.assertEqual(m.overlays[0]["stage"], "approaching")
            self.assertFalse(m.overlays[0]["triggered"])

    def test_lateral_bystander_is_ignored_by_velocity_gate(self):
        from collections import deque
        from services.recognition.worker import evaluate_hybrid_gate

        trajectory = deque(maxlen=16)
        settings = {"min_size": 100.0}
        f1 = np.array([40, 60, 120, 120] + [0] * 11, dtype=np.float32)
        f2 = np.array([220, 60, 120, 120] + [0] * 11, dtype=np.float32)
        evaluate_hybrid_gate(f1, (240, 320, 3), trajectory, settings, 10.0)
        gate = evaluate_hybrid_gate(f2, (240, 320, 3), trajectory, settings, 10.2)
        self.assertTrue(gate["is_lateral_passerby"])
        self.assertFalse(gate["kinematics_ready"])
        self.assertFalse(gate["triggered"])

    def test_side_face_exemplar_verifies_without_blocking_inference(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as d:
            side_vec = [0.0, 1.0] + [0.0] * 510
            p = dict(
                id="person",
                name="Officer",
                personnel_id="P01",
                ble_tag="tag",
                reference=[1.0] + [0.0] * 511,
                exemplars=[[1.0] + [0.0] * 511, side_vec],
            )
            m = self.make_monitor(d, [p])
            m.recognizer.feature.return_value = np.array(side_vec, dtype=np.float32)
            side_metrics = dict(
                sharpness=100,
                size=150,
                yaw=0.48,
                brightness=100,
                confidence=0.76,
                overexposed=0,
            )
            with (
                patch("services.recognition.worker.quality_metrics", return_value=side_metrics),
                patch(
                    "services.recognition.worker.time.monotonic",
                    side_effect=[t for t in [10, 10.2, 10.4] for _ in range(3)],
                ),
            ):
                m.process(np.zeros((240, 320, 3), np.uint8))
                # Before reaching min_samples=3, soft yaw guidance is shown while inference runs
                self.assertEqual(m.overlays[0]["label"], "Turn your face toward the camera.")
                m.process(np.zeros((240, 320, 3), np.uint8))
                m.process(np.zeros((240, 320, 3), np.uint8))
            self.assertEqual(m.latest["outcome"], "verified")
            self.assertTrue(m.overlays[0]["confirmed"])
            self.assertEqual(m.overlays[0]["label"], "Officer")


class PreviewTests(unittest.TestCase):
    def test_buffer_keeps_latest_frame_instead_of_a_queue(self):
        from services.recognition.worker import FrameBuffer

        buffer = FrameBuffer()
        buffer.start()
        buffer.publish("old", b"old-jpeg")
        buffer.publish("new", b"new-jpeg")
        frame, sequence, _ = buffer.latest()
        self.assertEqual(frame, "new")
        self.assertEqual(buffer.next_jpeg(0), (b"new-jpeg", sequence, True))
        buffer.stop()
        self.assertEqual(buffer.next_jpeg(sequence), (None, sequence, False))

    def test_preview_continues_during_blocked_recognition(self):
        import threading
        import time
        from unittest.mock import patch

        from services.recognition.worker import Monitor

        started = threading.Event()
        release = threading.Event()

        class Camera:
            def isOpened(self):
                return True

            def set(self, *args):
                pass

            def read(self):
                time.sleep(0.01)
                return True, np.zeros((120, 160, 3), np.uint8)

            def release(self):
                pass

        with (
            tempfile.TemporaryDirectory() as d,
            patch("services.recognition.worker.BLEScanner"),
            patch("services.recognition.worker.cv2.VideoCapture", return_value=Camera()),
        ):
            m = Monitor(Path(d), "test-token")
            m.sync = Mock()
            m.initialize = Mock()

            def slow_inference(frame):
                started.set()
                release.wait(timeout=2)

            m.process = slow_inference
            m.start_camera()
            capture = threading.Thread(target=m.capture_loop)
            recognition = threading.Thread(target=m.run)
            capture.start()
            recognition.start()
            try:
                self.assertTrue(started.wait(timeout=1))
                initial = m.buffer.sequence
                with m.buffer.condition:
                    advanced = m.buffer.condition.wait_for(
                        lambda: m.buffer.sequence >= initial + 5, timeout=1
                    )
                self.assertTrue(
                    advanced, "Preview stopped while recognition was blocked"
                )
                self.assertFalse(release.is_set())
            finally:
                m.stopping.set()
                release.set()
                capture.join(timeout=2)
                recognition.join(timeout=2)
            self.assertFalse(capture.is_alive())
            self.assertFalse(recognition.is_alive())

    def test_stop_wakes_waiting_preview_consumer(self):
        import threading

        from services.recognition.worker import FrameBuffer

        buffer = FrameBuffer()
        buffer.start()
        buffer.publish("frame", b"jpeg")
        result = []
        waiting = threading.Event()

        def wait():
            waiting.set()
            result.append(buffer.next_jpeg(buffer.sequence, timeout=5))

        thread = threading.Thread(target=wait)
        thread.start()
        self.assertTrue(waiting.wait(timeout=1))
        buffer.stop()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertFalse(result[0][2])


class EnrollmentTests(unittest.TestCase):
    def test_enrollment_uses_all_front_references_and_normalizes_embedding(self):
        frame = np.random.default_rng(10).integers(
            50, 230, (300, 300, 3), dtype=np.uint8
        )
        face = np.array(
            [40, 40, 200, 200, 80, 100, 180, 100, 130, 140, 100, 180, 160, 180, 0.99],
            np.float32,
        )
        low = face.copy()
        low[14] = 0.4
        recognizer = Mock()
        recognizer.feature.return_value = np.array([2.0] + [0.0] * 511, np.float32)
        job = dict(
            pose="front",
            baseline=[0, 0.5],
            references=[[0, 1] + [0] * 510, [1] + [0] * 511],
        )
        embedding, quality = enrollment_result(frame, [face, low], recognizer, job)
        self.assertAlmostEqual(np.linalg.norm(embedding), 1)
        self.assertEqual(quality["crop"], [0, 0, 280, 280])
        import json

        json.dumps(dict(embedding=embedding, quality=quality))
        shorter = face.copy()
        shorter[3] = 160
        _, short_quality = enrollment_result(frame, [shorter], recognizer, job)
        self.assertEqual(short_quality["crop"], [0, 0, 280, 240])
        job["references"] = [[0, 1] + [0] * 510]
        with self.assertRaisesRegex(ValueError, "Face changed"):
            enrollment_result(frame, [face], recognizer, job)

    def test_weak_beacons_are_visible_for_enrollment_but_cannot_grant_access(self):
        from services.recognition.ble_scanner import BLEScanner, ScanEntry, verify_tag
        from services.recognition.worker import Monitor

        m = Monitor.__new__(Monitor)
        m.scanner = BLEScanner()
        m.scanner.latest_results = {"a": ScanEntry("a", -90, [], {65535: b"test-tag"})}
        m.gallery = []
        state = m.ble_state()
        self.assertEqual(state["tags"], [{"tag": "test-tag", "rssi": -90}])
        self.assertFalse(verify_tag("test-tag", m.scanner.latest_results))

    def test_scan_button_sends_command_to_existing_bluetooth_owner(self):
        from services.recognition.ble_scanner import BLEScanner

        s = BLEScanner()
        s.enabled = True
        s.start = Mock()
        s._proc = Mock()
        s._proc.poll.return_value = None
        s.request_scan()
        s._proc.stdin.write.assert_called_once_with("scan\n")
        self.assertTrue(s.state()["scanning"])


class CameraLifetimeTests(unittest.TestCase):
    def test_multiple_tabs_and_reconnect_grace(self):
        from services.recognition.worker import MonitorViewers

        viewers = MonitorViewers()
        viewers.start(now=10)
        self.assertTrue(viewers.active(now=14.9))
        self.assertFalse(viewers.active(now=15))
        a, b = viewers.attach(), viewers.attach()
        self.assertTrue(viewers.active(now=100))
        viewers.detach(a, now=100)
        self.assertEqual(viewers.count(), 1)
        self.assertTrue(viewers.active(now=110))
        viewers.detach(b, now=110)
        self.assertTrue(viewers.active(now=111.9))
        self.assertFalse(viewers.active(now=112))
        c = viewers.attach()
        self.assertTrue(viewers.active(now=115))
        viewers.detach(c, now=115)
        self.assertFalse(viewers.active(now=117))

    def test_slow_camera_start_gets_fresh_preview_grace_after_first_frame(self):
        import threading
        import time
        from unittest.mock import patch

        from services.recognition.worker import Monitor

        first_frame = threading.Event()

        class Camera:
            def isOpened(self):
                return True

            def set(self, *args):
                pass

            def read(self):
                if not first_frame.is_set():
                    # Simulate the initial grace expiring during webcam startup.
                    m.viewers.deadline = time.monotonic() - 1
                    first_frame.set()
                time.sleep(0.01)
                return True, np.zeros((120, 160, 3), np.uint8)

            def release(self):
                pass

        with (
            tempfile.TemporaryDirectory() as d,
            patch("services.recognition.worker.BLEScanner"),
            patch("services.recognition.worker.cv2.VideoCapture", return_value=Camera()),
        ):
            m = Monitor(Path(d), "test-token")
            m.start_camera()
            capture = threading.Thread(target=m.capture_loop)
            capture.start()
            try:
                self.assertTrue(first_frame.wait(timeout=1))
                time.sleep(0.1)
                self.assertTrue(m.requested)
                self.assertTrue(m.viewers.active())
                self.assertIsNotNone(m.buffer.jpeg)
            finally:
                m.stopping.set()
                capture.join(timeout=2)
            self.assertFalse(capture.is_alive())

    def test_video_disconnect_does_not_stop_camera_but_last_page_close_does(self):
        import http.client
        import socket
        import threading
        import time
        from http.server import ThreadingHTTPServer
        from unittest.mock import patch

        from services.recognition.worker import Handler, Monitor

        released = threading.Event()
        opening = threading.Event()
        finish_open = threading.Event()

        class Camera:
            def isOpened(self):
                return True

            def set(self, *args):
                pass

            def read(self):
                time.sleep(0.01)
                return True, np.full((120, 160, 3), 100, np.uint8)

            def release(self):
                released.set()

        def until(check, timeout=4):
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if check():
                    return True
                time.sleep(0.02)
            return False

        def open_camera(_):
            opening.set()
            finish_open.wait(timeout=3)
            return Camera()

        with (
            tempfile.TemporaryDirectory() as d,
            patch("services.recognition.worker.BLEScanner"),
            patch("services.recognition.worker.cv2.VideoCapture", side_effect=open_camera),
        ):
            m = Monitor(Path(d), "test-token")
            m.scanner.enabled = False
            m.scanner.last_scan_time = None
            m.scanner.last_error = None
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server.monitor = m
            serving = threading.Thread(target=server.serve_forever, daemon=True)
            capture = threading.Thread(target=m.capture_loop, daemon=True)
            serving.start()
            capture.start()
            clients = []

            def open_preview():
                c = http.client.HTTPConnection(
                    "127.0.0.1", server.server_port, timeout=2
                )
                c.request(
                    "GET",
                    "/stream",
                    headers={
                        "Host": "127.0.0.1:8766",
                        "Authorization": "Bearer test-token",
                    },
                )
                response = c.getresponse()
                self.assertEqual(response.status, 200)
                # Headers and ownership must be established before camera ready.
                finish_open.set()
                self.assertTrue(response.read1(128).startswith(b"--frame"))
                clients.append((c, response))
                return c, response

            def close(pair):
                c, response = pair
                if c.sock:
                    try:
                        c.sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                response.close()
                c.close()

            def open_presence():
                c = http.client.HTTPConnection(
                    "127.0.0.1", server.server_port, timeout=2
                )
                c.request(
                    "GET",
                    "/presence",
                    headers={
                        "Host": "127.0.0.1:8766",
                        "Authorization": "Bearer test-token",
                    },
                )
                response = c.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    response.getheader("Content-Type"), "text/event-stream"
                )
                self.assertTrue(response.read1(128).startswith(b"data: connected"))
                clients.append((c, response))
                return c, response

            try:
                page_a, page_b = open_presence(), open_presence()
                m.start_camera()
                self.assertTrue(opening.wait(timeout=1))
                a = open_preview()
                b = open_preview()
                self.assertTrue(until(lambda: m.viewers.count() == 2))
                close(a)
                close(page_a)
                self.assertTrue(until(lambda: m.viewers.count() == 1))
                self.assertTrue(m.requested)
                self.assertFalse(released.is_set())
                close(b)
                # Wait longer than the previous video-disconnect grace period.
                self.assertFalse(released.wait(timeout=2.2))
                self.assertTrue(m.requested)
                self.assertEqual(m.viewers.count(), 1)
                # Reconnecting video does not change page ownership.
                retry = open_preview()
                self.assertEqual(m.viewers.count(), 1)
                close(retry)
                close(page_b)
                self.assertTrue(until(lambda: m.viewers.count() == 0))
                self.assertTrue(released.wait(timeout=4))
                self.assertFalse(m.requested)
                self.assertIn("last monitoring page", m.camera_note)
            finally:
                finish_open.set()
                for pair in clients:
                    close(pair)
                m.stopping.set()
                m.buffer.stop()
                server.shutdown()
                server.server_close()
                capture.join(timeout=2)
                serving.join(timeout=2)


class PhotoEvidenceTests(unittest.TestCase):
    make_monitor = VerificationDecisionTests.make_monitor
    exercise = VerificationDecisionTests.exercise

    def test_record_photos_survive_outbox_restart_without_polluting_status(self):
        import base64
        import json

        from services.recognition.photos import jpeg

        with tempfile.TemporaryDirectory() as d:
            registered = jpeg(np.full((140, 140, 3), 180, np.uint8))
            person = dict(
                id="person",
                name="Officer",
                personnel_id="P01",
                ble_tag="tag",
                reference=[1.0] + [0.0] * 511,
            )
            m = self.make_monitor(d, [person])
            m.registered_photo = Mock(return_value=registered)
            self.exercise(m, [10, 10.2, 10.4])
            with m.outbox.connection() as db:
                event = json.loads(
                    db.execute("SELECT payload FROM events").fetchone()[0]
                )
            self.assertEqual(base64.b64decode(event["registered_image"]), registered)
            captured = base64.b64decode(event["captured_image"])
            self.assertTrue(captured.startswith(b"\xff\xd8"))
            self.assertNotIn("captured_image", m.latest)
            self.assertNotIn("registered_image", m.latest)
            self.assertEqual(m.latest_images["captured"], captured)
            restored = Outbox(Path(d) / "outbox.sqlite3")
            api = Mock()
            api.call.side_effect = OSError("offline")
            with self.assertRaises(OSError):
                restored.flush(api)
            self.assertEqual(restored.count(), 1)
            api.call.side_effect = None
            restored.flush(api)
            self.assertEqual(
                api.call.call_args.args[1]["captured_image"], event["captured_image"]
            )
            self.assertEqual(restored.count(), 0)


class CameraRestartTests(unittest.TestCase):
    def test_stop_start_while_reading_reopens_camera_and_discards_old_frame(self):
        import threading
        import time
        from unittest.mock import patch

        from services.recognition.worker import Monitor

        reading = threading.Event()
        finish_read = threading.Event()
        cameras = []

        class Camera:
            def __init__(self, _):
                self.number = len(cameras) + 1
                self.reads = 0
                self.released = False
                cameras.append(self)

            def isOpened(self):
                return True

            def set(self, *args):
                pass

            def read(self):
                self.reads += 1
                if self.number == 1 and self.reads == 2:
                    reading.set()
                    finish_read.wait(timeout=3)
                time.sleep(0.01)
                return True, np.full((120, 160, 3), self.number, np.uint8)

            def release(self):
                self.released = True

        with (
            tempfile.TemporaryDirectory() as d,
            patch("services.recognition.worker.BLEScanner"),
            patch("services.recognition.worker.cv2.VideoCapture", side_effect=Camera),
        ):
            m = Monitor(Path(d), "test-token")
            m.viewers.attach()
            m.start_camera()
            old_session = m.camera_session
            thread = threading.Thread(target=m.capture_loop)
            thread.start()
            try:
                self.assertTrue(reading.wait(timeout=1))
                m.requested = False
                m.start_camera()
                self.assertNotEqual(m.camera_session, old_session)
                finish_read.set()
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    frame, _, generation = m.buffer.latest()
                    if generation == 2 and frame is not None:
                        break
                    time.sleep(0.01)
                self.assertEqual(generation, 2)
                self.assertTrue(cameras[0].released)
                self.assertTrue(np.all(frame == 2))
                self.assertEqual(m.capture_session, m.camera_session)
            finally:
                finish_read.set()
                m.stopping.set()
                thread.join(timeout=2)
            self.assertFalse(thread.is_alive())

    def test_model_initialization_is_atomic_and_retries_failed_load(self):
        from unittest.mock import patch

        from services.recognition.worker import Monitor

        m = Monitor.__new__(Monitor)
        m.detector = m.recognizer = None
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        m.runtime = Path(directory.name)
        m.pipeline = Mock()
        detector, recognizer = Mock(), Mock()
        with (
            patch("services.recognition.worker.RetinaFace", return_value=detector),
            patch(
                "services.recognition.worker.ArcFace",
                side_effect=[RuntimeError("model failed"), recognizer],
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "model failed"):
                m.initialize()
            self.assertIsNone(m.detector)
            self.assertIsNone(m.recognizer)
            self.assertEqual(m.recognition_status, "error")
            m.initialize()
            self.assertEqual(m.recognition_status, "ready")
            detector.detect.assert_called_once()
            recognizer.feature.assert_called_once()
            m.pipeline.associate.assert_called_once()
            m.pipeline.reset.assert_called_once()

    def test_previous_camera_session_cannot_publish_detection(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        m = VerificationDecisionTests.make_monitor(self, directory.name, [])
        # Return before any evidence can be emitted; the dummy detector supplies a face.
        m.processing_session = "old"
        m.camera_session = m.capture_session = "new"
        m.requested = True
        m.overlays = []
        m.process(np.zeros((240, 320, 3), np.uint8))
        self.assertEqual(m.overlays, [])
        self.assertIsNone(m.latest)
