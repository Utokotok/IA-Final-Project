import json
import tempfile
import uuid

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from services.recognition.face_models import MODEL_ID

from .models import (
    Enrollment,
    FaceProfile,
    Personnel,
    ProcessingJob,
    Sample,
    Verification,
)

VECTOR = [1.0] + [0.0] * 511


class ApplicationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.override = override_settings(
            MEDIA_ROOT=self.tmp.name, SERVICE_TOKEN="test-service"
        )
        self.override.enable()
        self.admin = get_user_model().objects.create_user(
            "admin", password="correct-test-password", is_staff=True
        )
        self.other = get_user_model().objects.create_user(
            "other", password="correct-test-password", is_staff=True
        )
        self.operator = get_user_model().objects.create_user(
            "operator", password="correct-test-password"
        )
        self.client = APIClient()
        self.client.force_login(self.admin)

    def tearDown(self):
        self.override.disable()
        self.tmp.cleanup()

    def person(self, **extra):
        return Personnel.objects.create(
            personnel_id="P-01",
            name="Test Officer",
            rank="PSSG",
            unit="ITMS",
            ble_tag="test-tag",
            **extra,
        )

    def service(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer test-service")
        return client

    def jpeg(self, shade=120):
        import cv2
        import numpy as np

        return cv2.imencode(".jpg", np.full((160, 160, 3), shade, np.uint8))[
            1
        ].tobytes()

    def test_event_photos_are_private_immutable_database_snapshots(self):
        import base64

        p = self.person(status="active")
        reference = self.jpeg(160)
        profile = FaceProfile.objects.create(
            person=p,
            model_id=MODEL_ID,
            reference=VECTOR,
            photo=reference,
            photo_taken_at=timezone.now(),
        )
        capture = self.jpeg(90)
        data = dict(
            id=str(uuid.uuid4()),
            timestamp=timezone.now().isoformat(),
            direction="entry",
            outcome="face_mismatch",
            person_id=str(p.id),
            snapshot={"name": p.name, "ble_tag": p.ble_tag},
            similarity=0.4,
            evidence={
                "profile_version": str(profile.version),
                "settings": {"threshold": 0.6},
            },
            captured_image=base64.b64encode(capture).decode(),
            registered_image=base64.b64encode(reference).decode(),
            registered_at=profile.photo_taken_at.isoformat(),
        )
        service = self.service()
        response = service.post("/api/service/events/", data, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        event = Verification.objects.get(pk=data["id"])
        self.assertEqual(bytes(event.captured_image), capture)
        self.assertEqual(bytes(event.registered_image), reference)
        profile.photo = self.jpeg(220)
        profile.version = uuid.uuid4()
        profile.save()
        p.name = "Changed after event"
        p.save()
        data["captured_image"] = base64.b64encode(self.jpeg(10)).decode()
        self.assertFalse(
            service.post("/api/service/events/", data, format="json").json()["created"]
        )
        event.refresh_from_db()
        self.assertEqual(bytes(event.captured_image), capture)
        detail = self.client.get(f"/api/logs/{event.id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertNotIn("captured_image", detail.json())
        self.assertEqual(detail.json()["name"], "Test Officer")
        self.assertIsNotNone(detail.json()["captured_image_url"])
        for kind, expected in [("captured", capture), ("registered", reference)]:
            url = f"/api/logs/{event.id}/images/{kind}/"
            image = self.client.get(url)
            self.assertEqual(image.content, expected)
            self.assertEqual(image["Content-Type"], "image/jpeg")
            self.assertIn("no-store", image["Cache-Control"])
            self.assertEqual(APIClient().get(url).status_code, 403)
            operator = APIClient()
            operator.force_login(self.operator)
            self.assertEqual(operator.get(url).status_code, 403)

    def test_bad_photos_are_rejected_and_legacy_logs_have_no_fake_capture(self):
        import base64

        data = dict(
            id=str(uuid.uuid4()),
            timestamp=timezone.now().isoformat(),
            direction="exit",
            outcome="missing_ble",
            captured_image=base64.b64encode(b"not a jpeg").decode(),
        )
        service = self.service()
        self.assertEqual(
            service.post("/api/service/events/", data, format="json").status_code, 400
        )
        self.assertFalse(Verification.objects.exists())
        data["captured_image"] = "x" * 700000
        self.assertEqual(
            service.post("/api/service/events/", data, format="json").status_code, 400
        )
        data.pop("captured_image")
        self.assertEqual(
            service.post("/api/service/events/", data, format="json").status_code, 200
        )
        event = Verification.objects.get(pk=data["id"])
        detail = self.client.get(f"/api/logs/{event.id}/").json()
        self.assertIsNone(detail["captured_image_url"])
        self.assertIsNone(detail["registered_image_url"])
        self.assertEqual(
            self.client.get(f"/api/logs/{event.id}/images/captured/").status_code, 404
        )

    def test_offline_event_keeps_old_reference_photo_after_new_profile(self):
        import base64

        p = self.person(status="active")
        old_photo = self.jpeg(80)
        profile = FaceProfile.objects.create(
            person=p, model_id=MODEL_ID, reference=VECTOR, photo=self.jpeg(200)
        )
        data = dict(
            id=str(uuid.uuid4()),
            timestamp=timezone.now().isoformat(),
            direction="entry",
            outcome="verified",
            person_id=str(p.id),
            evidence={"profile_version": str(uuid.uuid4())},
            captured_image=base64.b64encode(self.jpeg(100)).decode(),
            registered_image=base64.b64encode(old_photo).decode(),
        )
        self.assertEqual(
            self.service()
            .post("/api/service/events/", data, format="json")
            .status_code,
            200,
        )
        self.assertEqual(
            bytes(Verification.objects.get(pk=data["id"]).registered_image), old_photo
        )
        data["id"] = str(uuid.uuid4())
        data.pop("registered_image")
        self.assertEqual(
            self.service()
            .post("/api/service/events/", data, format="json")
            .status_code,
            200,
        )
        self.assertIsNone(Verification.objects.get(pk=data["id"]).registered_image)
        data["id"] = str(uuid.uuid4())
        data["evidence"]["profile_version"] = str(profile.version)
        self.assertEqual(
            self.service()
            .post("/api/service/events/", data, format="json")
            .status_code,
            200,
        )
        self.assertEqual(
            bytes(Verification.objects.get(pk=data["id"]).registered_image),
            bytes(profile.photo),
        )

    def test_registration_photo_service_is_private_and_version_bound(self):
        p = self.person(status="active")
        photo = self.jpeg()
        profile = FaceProfile.objects.create(
            person=p, model_id=MODEL_ID, reference=VECTOR, photo=photo
        )
        url = f"/api/service/people/{p.id}/photo/{profile.version}/"
        self.assertEqual(self.service().get(url).content, photo)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(
            self.service().get(url, REMOTE_ADDR="192.168.1.20").status_code, 403
        )
        self.assertEqual(
            self.service()
            .get(f"/api/service/people/{p.id}/photo/{uuid.uuid4()}/")
            .status_code,
            404,
        )
        self.assertEqual(self.client.get(f"/api/people/{p.id}/photo/").content, photo)

    def test_backfill_restores_only_matching_registration_versions(self):
        import io

        from django.core.management import call_command

        p = self.person(status="active")
        photo = self.jpeg()
        profile = FaceProfile.objects.create(
            person=p, model_id=MODEL_ID, reference=VECTOR, photo=photo
        )
        common = dict(
            person=p,
            name=p.name,
            timestamp=timezone.now(),
            direction="entry",
            outcome="verified",
        )
        matched = Verification.objects.create(
            id=uuid.uuid4(),
            evidence={"profile_version": str(profile.version)},
            **common,
        )
        old = Verification.objects.create(
            id=uuid.uuid4(), evidence={"profile_version": str(uuid.uuid4())}, **common
        )
        call_command("backfill_profile_photos", stdout=io.StringIO())
        matched.refresh_from_db()
        old.refresh_from_db()
        self.assertEqual(bytes(matched.registered_image), photo)
        self.assertIsNone(matched.captured_image)
        self.assertIsNone(old.registered_image)
        profile.photo = self.jpeg(200)
        profile.save()
        call_command("backfill_profile_photos", stdout=io.StringIO())
        matched.refresh_from_db()
        self.assertEqual(bytes(matched.registered_image), photo)

    def test_history_url_supports_direct_open_and_refresh(self):
        from pathlib import Path

        root = Path(self.tmp.name)
        folder = root / "frontend" / "dist"
        folder.mkdir(parents=True)
        (folder / "index.html").write_text("<html>History application</html>")
        with override_settings(ROOT=root):
            response = self.client.get(f"/logs/{uuid.uuid4()}")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"History application", b"".join(response.streaming_content))

    def test_photo_payloads_are_not_selected_by_log_lists(self):
        from access.photos import events

        query = str(events().query)
        # Availability checks may name the columns, but raw blobs are deferred.
        self.assertIn("captured_image", events().query.deferred_loading[0])
        self.assertIn("registered_image", events().query.deferred_loading[0])
        self.assertIn("CASE WHEN", query)

    def test_authentication_and_roles(self):
        anon = APIClient()
        self.assertEqual(anon.get("/api/people/").status_code, 403)
        anon.force_login(self.operator)
        self.assertEqual(anon.get("/api/people/").status_code, 403)
        self.assertEqual(anon.get("/api/logs/?export=1").status_code, 403)

    def test_login_requires_csrf_and_rotates_session(self):
        c = Client(enforce_csrf_checks=True)
        self.assertEqual(
            c.post(
                "/api/login/",
                json.dumps({"username": "admin", "password": "correct-test-password"}),
                content_type="application/json",
                secure=True,
            ).status_code,
            403,
        )
        token = c.get("/api/session/", secure=True).json()["csrf"]
        r = c.post(
            "/api/login/",
            json.dumps({"username": "admin", "password": "correct-test-password"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_ORIGIN="https://testserver",
            secure=True,
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["user"]["username"], "admin")

    def test_login_throttling(self):
        c = Client()
        for _ in range(8):
            self.assertEqual(
                c.post(
                    "/api/login/",
                    json.dumps({"username": "admin", "password": "wrong"}),
                    content_type="application/json",
                ).status_code,
                400,
            )
        self.assertEqual(
            c.post(
                "/api/login/",
                json.dumps({"username": "admin", "password": "correct-test-password"}),
                content_type="application/json",
            ).status_code,
            429,
        )

    def test_personnel_create_and_normalized_beacon_uniqueness(self):
        self.person()
        data = dict(
            personnel_id="P-02",
            name="Other Officer",
            rank="PCPT",
            unit="ITMS",
            ble_tag="TEST-TAG",
        )
        r = self.client.post("/api/people/", data, format="json")
        self.assertEqual(r.status_code, 400)
        data["ble_tag"] = "second"
        r = self.client.post("/api/people/", data, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["status"], "draft")
        self.assertEqual(
            Personnel.objects.get(personnel_id="P-02").status, "incomplete"
        )

    def test_enrollment_ownership(self):
        e = Enrollment.objects.create(person=self.person(), owner=self.other)
        self.assertEqual(self.client.get(f"/api/enrollments/{e.id}/").status_code, 404)

    def test_incomplete_enrollment_cannot_activate(self):
        e = Enrollment.objects.create(person=self.person(), owner=self.admin)
        self.assertEqual(
            self.client.post(
                f"/api/enrollments/{e.id}/", {}, format="json"
            ).status_code,
            400,
        )
        e.person.refresh_from_db()
        self.assertEqual(e.person.status, "incomplete")

    def test_completed_enrollment_activates_profile(self):
        e = Enrollment.objects.create(person=self.person(), owner=self.admin)
        for pose in ["front", "left", "right", "up", "down"]:
            for _ in range(8):
                sample = Sample(enrollment=e, pose=pose, embedding=VECTOR)
                sample.image.save(
                    str(uuid.uuid4()) + ".jpg", ContentFile(self.jpeg()), save=False
                )
                sample.save()
        r = self.client.post(f"/api/enrollments/{e.id}/", {}, format="json")
        self.assertEqual(r.status_code, 200)
        e.person.refresh_from_db()
        self.assertEqual(e.person.status, "active")
        self.assertEqual(len(e.person.profile.features), 40)

    def test_service_secret_and_locality(self):
        self.assertEqual(self.client.get("/api/service/gallery/").status_code, 403)
        self.assertEqual(
            self.service()
            .get("/api/service/gallery/", REMOTE_ADDR="192.168.1.2")
            .status_code,
            403,
        )
        self.assertEqual(self.service().get("/api/service/gallery/").status_code, 200)

    def test_deactivation_removes_gallery_but_preserves_history(self):
        p = self.person(status="active")
        FaceProfile.objects.create(
            person=p, model_id=MODEL_ID, reference=VECTOR, features=[VECTOR] * 40
        )
        c = self.service()
        before = c.get("/api/service/gallery/").json()
        self.assertEqual(len(before["people"]), 1)
        event = Verification.objects.create(
            id=uuid.uuid4(),
            person=p,
            name=p.name,
            timestamp=timezone.now(),
            direction="entry",
            outcome="verified",
        )
        self.assertEqual(self.client.delete(f"/api/people/{p.id}/").status_code, 200)
        after = c.get("/api/service/gallery/").json()
        self.assertEqual(after["people"], [])
        self.assertNotEqual(before["version"], after["version"])
        self.assertTrue(Verification.objects.filter(pk=event.pk).exists())

    def test_event_ingestion_is_idempotent(self):
        data = dict(
            id=str(uuid.uuid4()),
            timestamp=timezone.now().isoformat(),
            direction="entry",
            outcome="missing_ble",
            evidence={},
        )
        c = self.service()
        self.assertTrue(
            c.post("/api/service/events/", data, format="json").json()["created"]
        )
        self.assertFalse(
            c.post("/api/service/events/", data, format="json").json()["created"]
        )
        self.assertEqual(Verification.objects.count(), 1)

    def test_invalid_event_rejected(self):
        c = self.service()
        data = dict(
            id=str(uuid.uuid4()),
            timestamp="invalid",
            direction="sideways",
            outcome="verified",
        )
        self.assertEqual(
            c.post("/api/service/events/", data, format="json").status_code, 400
        )

    def test_browser_jpeg_capture_keeps_csrf_and_queues_a_job(self):
        import cv2
        import numpy as np

        e = Enrollment.objects.create(person=self.person(), owner=self.admin)
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.admin)
        token = c.get("/api/session/", secure=True).json()["csrf"]
        jpeg = cv2.imencode(".jpg", np.full((240, 320, 3), 120, np.uint8))[1].tobytes()
        url = f"/api/enrollments/{e.id}/capture/?pose=front"
        self.assertEqual(
            c.post(url, jpeg, content_type="image/jpeg", secure=True).status_code, 403
        )
        headers = dict(
            HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN="https://testserver", secure=True
        )
        self.assertEqual(
            c.post(url, b"bad jpeg", content_type="image/jpeg", **headers).status_code,
            400,
        )
        self.assertEqual(
            c.post(
                url, b"x" * (2 * 1024 * 1024 + 1), content_type="image/jpeg", **headers
            ).status_code,
            400,
        )
        response = c.post(url, jpeg, content_type="image/jpeg", **headers)
        self.assertEqual(response.status_code, 202, response.content)
        job = ProcessingJob.objects.get(pk=response.json()["job_id"])
        self.assertEqual(job.status, "pending")
        claimed = (
            self.service()
            .post("/api/service/jobs/claim/", {}, format="json")
            .json()["job"]
        )
        result = self.service().post(
            f"/api/service/jobs/{job.id}/complete/",
            {
                "lease": claimed["lease"],
                "embedding": VECTOR,
                "quality": {"crop": [20, 20, 180, 180], "yaw": 0, "pitch": 0.5},
            },
            format="json",
        )
        self.assertEqual(result.status_code, 200, result.content)
        sample = Sample.objects.get(enrollment=e)
        with sample.image.open("rb") as f:
            image = cv2.imdecode(np.frombuffer(f.read(), np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(image.shape[:2], (160, 160))
        self.assertEqual(
            c.get(f"/api/jobs/{job.id}/", secure=True).json()["enrollment"]["counts"][
                "front"
            ],
            1,
        )

    def test_ble_scan_is_admin_only_and_requests_worker_scan(self):
        from unittest.mock import MagicMock, patch

        response = MagicMock()
        response.__enter__.return_value.read.return_value = (
            b'{"tags":[],"enabled":true,"scanning":true}'
        )
        with patch("access.views.urlopen", return_value=response) as remote:
            self.assertEqual(
                self.client.post("/api/ble/", {}, format="json").status_code, 200
            )
            req = remote.call_args.args[0]
            self.assertEqual(req.full_url, "http://127.0.0.1:8766/ble")
            self.assertEqual(req.get_method(), "POST")
            self.assertEqual(req.get_header("Authorization"), "Bearer test-service")
        anon = APIClient()
        self.assertEqual(anon.get("/api/ble/").status_code, 403)
        anon.force_login(self.operator)
        self.assertEqual(anon.post("/api/ble/", {}, format="json").status_code, 403)

    def test_jobs_use_leases_and_invalidated_captures_cannot_complete(self):
        e = Enrollment.objects.create(person=self.person(), owner=self.admin)
        job = ProcessingJob(enrollment=e, pose="front", revision=0)
        job.image.save("capture.jpg", ContentFile(b"jpeg"))
        job.save()
        c = self.service()
        claimed = c.post("/api/service/jobs/claim/", {}, format="json").json()["job"]
        self.assertIsNotNone(claimed)
        self.assertEqual(
            c.post(
                f"/api/service/jobs/{job.id}/complete/",
                {"lease": str(uuid.uuid4()), "embedding": VECTOR},
                format="json",
            ).status_code,
            400,
        )
        self.client.delete(f"/api/enrollments/{e.id}/poses/front/")
        self.assertEqual(
            c.post(
                f"/api/service/jobs/{job.id}/complete/",
                {"lease": claimed["lease"], "embedding": VECTOR},
                format="json",
            ).status_code,
            400,
        )
        self.assertEqual(Sample.objects.count(), 0)

    def test_accept_capture_and_duplicate_callback(self):
        e = Enrollment.objects.create(person=self.person(), owner=self.admin)
        job = ProcessingJob(enrollment=e, pose="front", revision=0)
        job.image.save("capture.jpg", ContentFile(b"jpeg"))
        job.save()
        c = self.service()
        claimed = c.post("/api/service/jobs/claim/", {}, format="json").json()["job"]
        body = {
            "lease": claimed["lease"],
            "embedding": VECTOR,
            "quality": {"yaw": 0.0, "pitch": 0.5},
        }
        self.assertEqual(
            c.post(
                f"/api/service/jobs/{job.id}/complete/", body, format="json"
            ).status_code,
            200,
        )
        self.assertEqual(
            c.post(
                f"/api/service/jobs/{job.id}/complete/", body, format="json"
            ).status_code,
            400,
        )
        self.assertEqual(Sample.objects.count(), 1)

    def test_local_monitor_rejects_lan_requests(self):
        self.assertEqual(
            self.client.get("/api/monitor/", REMOTE_ADDR="192.168.100.20").status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                "/api/monitor/frame/", REMOTE_ADDR="192.168.100.20"
            ).status_code,
            403,
        )

    def test_export_filters_and_formula_escape(self):
        for n, outcome in enumerate(["verified", "missing_ble"]):
            Verification.objects.create(
                id=uuid.uuid4(),
                name="=cmd()",
                timestamp=timezone.now(),
                direction="entry",
                outcome=outcome,
            )
        r = self.client.get("/api/logs/?outcome=missing_ble&export=1")
        self.assertEqual(r.status_code, 200)
        self.assertIn("'=cmd()", r.content.decode())
        self.assertEqual(len(r.content.decode().splitlines()), 2)
        self.assertEqual(self.client.get("/api/logs/?start=nonsense").status_code, 400)

    def test_review_requires_notes_and_records_actor(self):
        e = Verification.objects.create(
            id=uuid.uuid4(),
            timestamp=timezone.now(),
            direction="entry",
            outcome="missing_ble",
        )
        self.assertEqual(
            self.client.post(
                f"/api/logs/{e.id}/review/", {"note": ""}, format="json"
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                f"/api/logs/{e.id}/review/",
                {"note": "Checked the beacon battery."},
                format="json",
            ).status_code,
            200,
        )
        e.refresh_from_db()
        self.assertEqual(e.reviewed_by, self.admin)

    def test_summary_does_not_invent_occupancy(self):
        self.assertIsNone(self.client.get("/api/dashboard/").json()["occupancy"])

    def test_person_images_require_login(self):
        e = Enrollment.objects.create(person=self.person(), owner=self.admin)
        sample = Sample.objects.create(
            enrollment=e, pose="front", image="private.jpg", embedding=VECTOR
        )
        self.assertEqual(APIClient().get(f"/api/samples/{sample.id}/").status_code, 403)

    def test_service_polling_is_not_anonymous_login_traffic(self):
        c = self.service()
        for _ in range(80):
            self.assertEqual(c.get("/api/service/gallery/").status_code, 200)

    def test_delayed_events_preserve_capture_identity(self):
        p = self.person()
        data = {
            "id": str(uuid.uuid4()),
            "timestamp": timezone.now().isoformat(),
            "direction": "entry",
            "outcome": "verified",
            "person_id": str(p.id),
            "snapshot": {
                "name": p.name,
                "personnel_id": p.personnel_id,
                "ble_tag": p.ble_tag,
            },
        }
        original = p.name
        p.name = "Renamed after capture"
        p.save()
        self.assertEqual(
            self.service()
            .post("/api/service/events/", data, format="json")
            .status_code,
            200,
        )
        self.assertEqual(Verification.objects.get(pk=data["id"]).name, original)

    def test_reviewed_import_activation_requires_complete_fields(self):
        p = self.person()
        FaceProfile.objects.create(
            person=p, model_id=MODEL_ID, reference=VECTOR, features=[VECTOR] * 40
        )
        self.assertEqual(
            self.client.post(f"/api/people/{p.id}/", {}, format="json").status_code, 200
        )
        p.refresh_from_db()
        self.assertEqual(p.status, "active")
        p.status = "incomplete"
        p.rank = ""
        p.save()
        self.assertEqual(
            self.client.post(f"/api/people/{p.id}/", {}, format="json").status_code, 400
        )

    def test_password_change_checks_old_password_and_policy(self):
        self.assertEqual(
            self.client.post(
                "/api/password/",
                {"old_password": "wrong", "password": "new-strong-password-123"},
                format="json",
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/password/",
                {"old_password": "correct-test-password", "password": "short"},
                format="json",
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/password/",
                {
                    "old_password": "correct-test-password",
                    "password": "new-strong-password-123",
                },
                format="json",
            ).status_code,
            200,
        )
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password("new-strong-password-123"))

    def test_page_presence_requires_login_and_local_client(self):
        self.assertEqual(APIClient().get("/api/monitor/presence/").status_code, 403)
        self.assertEqual(
            self.client.get(
                "/api/monitor/presence/", REMOTE_ADDR="192.168.100.20"
            ).status_code,
            403,
        )

    def test_page_presence_is_independent_of_video_and_closes_upstream(self):
        from unittest.mock import Mock, patch

        upstream = Mock()
        upstream.read1.side_effect = [b"data: connected\n\n", b": heartbeat\n\n"]
        with patch("access.views.urlopen", return_value=upstream) as connect:
            response = self.client.get(
                "/api/monitor/presence/", HTTP_ACCEPT="text/event-stream"
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "text/event-stream")
            self.assertEqual(
                connect.call_args.args[0].full_url, "http://127.0.0.1:8766/presence"
            )
            chunks = iter(response.streaming_content)
            self.assertEqual(next(chunks), b"data: connected\n\n")
            response.close()
            upstream.close.assert_called_once()

    def test_stream_requires_login_and_local_client(self):
        self.assertEqual(APIClient().get("/api/monitor/stream/").status_code, 403)
        self.assertEqual(
            self.client.get(
                "/api/monitor/stream/", REMOTE_ADDR="192.168.100.20"
            ).status_code,
            403,
        )

    def test_preview_forwards_camera_session_and_rejects_invalid_session(self):
        import uuid
        from unittest.mock import Mock, patch

        session = uuid.uuid4().hex
        upstream = Mock()
        upstream.read1.return_value = b""
        with patch("access.views.urlopen", return_value=upstream) as connect:
            response = self.client.get("/api/monitor/stream/", {"session": session})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                connect.call_args.args[0].full_url,
                "http://127.0.0.1:8766/stream?session=" + session,
            )
            list(response.streaming_content)
            self.assertEqual(
                self.client.get("/api/monitor/stream/", {"session": "bad"}).status_code,
                400,
            )

    def test_stream_is_forwarded_without_buffering_and_closes_upstream(self):
        from unittest.mock import Mock, patch

        upstream = Mock()
        upstream.read1.side_effect = [b"--frame\r\nfirst", b"--frame\r\nsecond", b""]
        with patch("access.views.urlopen", return_value=upstream):
            response = self.client.get(
                "/api/monitor/stream/", HTTP_ACCEPT="image/avif,image/webp,image/*"
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.streaming)
            self.assertEqual(response["X-Accel-Buffering"], "no")
            self.assertIn("multipart/x-mixed-replace", response["Content-Type"])
            self.assertEqual(
                b"".join(response.streaming_content),
                b"--frame\r\nfirst--frame\r\nsecond",
            )
            upstream.close.assert_called_once()
