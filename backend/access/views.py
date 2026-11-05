import csv
import hashlib
import io
import json
import math
import secrets
import uuid
from datetime import datetime, time, timedelta
from urllib.error import URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import BooleanField, Case, Q, Value, When
from django.http import FileResponse, HttpResponse, JsonResponse, StreamingHttpResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods
from rest_framework.authentication import BaseAuthentication
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    parser_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.exceptions import (
    AuthenticationFailed,
    PermissionDenied,
    ValidationError,
)
from rest_framework.parsers import BaseParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from services.recognition.face_models import MODEL_ID, pose_exemplars, reference_embedding
from services.recognition.photos import decode_image

from .models import (
    AuditEvent,
    Enrollment,
    FaceProfile,
    Personnel,
    ProcessingJob,
    Sample,
    Station,
    Verification,
)
from .photos import events, registration_photo
from .serializers import PersonnelSerializer, VerificationSerializer

POSES = ["front", "left", "right", "up", "down"]
OUTCOMES = [
    "verified",
    "face_mismatch",
    "missing_ble",
    "unknown_tag",
    "unidentified",
    "ambiguous",
    "timeout",
    "inactive",
    "service_unavailable",
]


def admin(request):
    if not request.user.is_staff:
        raise PermissionDenied("Administrator access required.")


def audit(request, action, target="", details=None):
    AuditEvent.objects.create(
        actor=request.user if request.user.is_authenticated else None,
        action=action,
        target=str(target),
        details=details or {},
    )


def vector(value):
    if (
        not isinstance(value, list)
        or len(value) != 512
        or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in value)
    ):
        raise ValidationError("Invalid embedding.")
    norm = sum(v * v for v in value) ** 0.5
    if not 0.99 < norm < 1.01:
        raise ValidationError("Embedding must be normalized.")
    return value


def gallery():
    rows = []
    for p in (
        Personnel.objects.filter(status="active")
        .select_related("profile")
        .annotate(
            reference_photo_available=Case(
                When(profile__photo__isnull=False, then=Value(True)),
                default=Value(False),
                output_field=BooleanField(),
            )
        )
        .defer("profile__photo")
    ):
        if not hasattr(p, "profile") or p.profile.model_id != MODEL_ID:
            continue
        exemplars = None
        if p.profile.features and len(p.profile.features) >= 8:
            try:
                exemplars = pose_exemplars(p.profile.features).tolist()
            except ValueError:
                exemplars = None
        row = dict(
            id=str(p.id),
            name=p.name,
            personnel_id=p.personnel_id,
            rank=p.rank,
            unit=p.unit,
            ble_tag=p.ble_tag,
            reference=p.profile.reference,
            profile_version=str(p.profile.version),
            photo_available=p.reference_photo_available,
            registered_at=p.profile.photo_taken_at.isoformat()
            if p.profile.photo_taken_at
            else None,
        )
        if exemplars is not None:
            row["exemplars"] = exemplars
        rows.append(row)
    rows.sort(key=lambda p: p["id"])
    version = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    return dict(people=rows, version=version, model_id=MODEL_ID)


def enrollment_data(e):
    samples = list(e.samples.order_by("created_at"))
    return dict(
        id=str(e.id),
        person=PersonnelSerializer(e.person).data,
        status=e.status,
        revision=e.revision,
        counts={p: sum(s.pose == p for s in samples) for p in POSES},
        samples=[
            dict(id=str(s.id), pose=s.pose, url=f"/api/samples/{s.id}/")
            for s in samples
        ],
        pending=e.jobs.filter(status__in=["pending", "processing"]).exists(),
    )


def own_enrollment(request, pk, lock=False):
    qs = Enrollment.objects.select_related("person")
    if lock:
        qs = qs.select_for_update(of=("self",))
    return get_object_or_404(qs, pk=pk, owner=request.user)


@require_http_methods(["GET"])
def session(request):
    csrf = get_token(request)
    return JsonResponse(
        dict(
            user={"username": request.user.username, "is_admin": request.user.is_staff}
            if request.user.is_authenticated
            else None,
            csrf=csrf,
        )
    )


@csrf_protect
@require_http_methods(["POST"])
def login(request):
    try:
        body = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({"error": "Invalid request."}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({"error": "Invalid request."}, status=400)
    key = "login:" + request.META.get("REMOTE_ADDR", "")
    failures = cache.get(key, 0)
    if failures >= 8:
        return JsonResponse({"error": "Too many attempts. Wait 5 minutes."}, status=429)
    username, password = body.get("username"), body.get("password")
    if not isinstance(username, str) or not isinstance(password, str):
        return JsonResponse({"error": "Enter your username and password."}, status=400)
    user = authenticate(request, username=username[:150], password=password)
    if user is None:
        cache.set(key, failures + 1, 300)
        return JsonResponse({"error": "Invalid username or password."}, status=400)
    auth_login(request, user)
    cache.delete(key)
    audit(request, "login")
    return JsonResponse(
        dict(
            user={"username": user.username, "is_admin": user.is_staff},
            csrf=get_token(request),
        )
    )


@api_view(["POST"])
def logout(request):
    audit(request, "logout")
    auth_logout(request)
    return Response({"ok": True})


@api_view(["GET", "POST"])
def people(request):
    admin(request)
    if request.method == "POST":
        ser = PersonnelSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            person = ser.save()
            e = Enrollment.objects.create(owner=request.user, person=person)
            audit(request, "personnel.create", person.id)
        return Response(enrollment_data(e), status=201)
    q = request.query_params.get("q", "")[:120]
    qs = Personnel.objects.all().order_by("-created_at")
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(personnel_id__icontains=q)
            | Q(unit__icontains=q)
            | Q(ble_tag__icontains=q)
        )
    return Response(
        {
            "people": PersonnelSerializer(qs, many=True).data,
            "total": Personnel.objects.count(),
            "active": Personnel.objects.filter(status="active").count(),
            "incomplete": Personnel.objects.filter(status="incomplete").count(),
            "drafts": [
                enrollment_data(e)
                for e in Enrollment.objects.filter(
                    owner=request.user, status="draft"
                ).select_related("person")
            ],
        }
    )


@api_view(["PATCH", "DELETE", "POST"])
def person(request, pk):
    admin(request)
    with transaction.atomic():
        p = get_object_or_404(Personnel.objects.select_for_update(), pk=pk)
        before = PersonnelSerializer(p).data
        if request.method == "POST":
            if (
                not hasattr(p, "profile")
                or p.profile.model_id != MODEL_ID
                or not all([p.personnel_id, p.name, p.rank, p.unit, p.ble_tag])
                or p.personnel_id.startswith("LEGACY-")
            ):
                raise ValidationError(
                    "Complete official personnel information and BLE assignment before activating a reviewed profile."
                )
            p.status = "active"
            p.save()
            audit(request, "personnel.activate", p.id)
        elif request.method == "DELETE":
            p.status = "inactive"
            p.save()
            audit(request, "personnel.deactivate", p.id, {"before": before})
        else:
            ser = PersonnelSerializer(p, data=request.data, partial=True)
            ser.is_valid(raise_exception=True)
            ser.save()
            audit(
                request, "personnel.edit", p.id, {"before": before, "after": ser.data}
            )
    return Response(PersonnelSerializer(p).data)


@api_view(["GET", "POST", "DELETE"])
def enrollment(request, pk):
    admin(request)
    with transaction.atomic():
        e = own_enrollment(request, pk, lock=True)
        if request.method == "DELETE":
            e.status = "cancelled"
            e.revision += 1
            e.save()
            e.person.status = "inactive"
            e.person.save()
            audit(request, "enrollment.cancel", e.id)
        elif request.method == "POST":
            if e.status != "draft":
                raise ValidationError("Enrollment is no longer editable.")
            if not all(
                [
                    e.person.name,
                    e.person.personnel_id,
                    e.person.rank,
                    e.person.unit,
                    e.person.ble_tag,
                ]
            ):
                raise ValidationError(
                    "Complete personnel information and BLE assignment."
                )
            if e.jobs.filter(status__in=["pending", "processing"]).exists():
                raise ValidationError("Wait for processing to finish.")
            samples = list(e.samples.order_by("created_at"))
            if any(sum(s.pose == p for s in samples) != 8 for p in POSES):
                raise ValidationError(
                    "Capture eight accepted samples at all five angles."
                )
            import numpy as np

            features = np.asarray([s.embedding for s in samples], dtype=np.float32)
            reference = reference_embedding(features)
            vector(reference.tolist())
            photo, photo_taken_at = registration_photo(e.person)
            if photo is None:
                raise ValidationError(
                    "The front registration photo is missing. Retake the front angle."
                )
            FaceProfile.objects.update_or_create(
                person=e.person,
                defaults=dict(
                    model_id=MODEL_ID,
                    photo=photo,
                    photo_taken_at=photo_taken_at,
                    reference=reference.tolist(),
                    features=features.tolist(),
                    version=uuid.uuid4(),
                ),
            )
            e.status = "complete"
            e.save()
            e.person.status = "active"
            e.person.save()
            audit(request, "enrollment.activate", e.person.id)
        return Response(enrollment_data(e))


class JPEGParser(BaseParser):
    media_type = "image/jpeg"

    def parse(self, stream, media_type=None, parser_context=None):
        data = stream.read(2 * 1024 * 1024 + 1)
        if not 0 < len(data) <= 2 * 1024 * 1024:
            raise ValidationError("Send a JPEG no larger than 2 MB.")
        return data


@api_view(["POST"])
@parser_classes([JPEGParser])
def capture(request, pk):
    admin(request)
    pose = request.query_params.get("pose")
    if pose not in POSES:
        raise ValidationError("Invalid pose.")
    data = request.data
    if not isinstance(data, bytes) or not 0 < len(data) <= 2 * 1024 * 1024:
        raise ValidationError("Send a JPEG no larger than 2 MB.")
    import cv2
    import numpy as np

    frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if frame is None or max(frame.shape[:2]) > 1280:
        raise ValidationError("Send a valid JPEG at most 1280 pixels per side.")
    with transaction.atomic():
        e = own_enrollment(request, pk, lock=True)
        if e.status != "draft":
            raise ValidationError("Enrollment is no longer editable.")
        if pose != "front" and e.samples.filter(pose="front").count() != 8:
            raise ValidationError("Complete the front angle before other poses.")
        if e.samples.filter(pose=pose).count() >= 8:
            raise ValidationError("This angle is complete.")
        if e.jobs.filter(status__in=["pending", "processing"]).exists():
            raise ValidationError("Previous capture is still processing.")
        job = ProcessingJob(enrollment=e, pose=pose, revision=e.revision)
        job.image.save(f"{uuid.uuid4()}.jpg", ContentFile(data), save=False)
        job.save()
    return Response({"job_id": str(job.id)}, status=202)


@api_view(["GET"])
def job_status(request, pk):
    admin(request)
    job = get_object_or_404(
        ProcessingJob.objects.select_related("enrollment"),
        pk=pk,
        enrollment__owner=request.user,
    )
    return Response(
        dict(
            status=job.status,
            error=job.error,
            enrollment=enrollment_data(job.enrollment),
        )
    )


@api_view(["DELETE"])
def retake(request, pk, pose):
    admin(request)
    if pose not in POSES:
        raise ValidationError("Invalid pose.")
    with transaction.atomic():
        e = own_enrollment(request, pk, lock=True)
        if e.status != "draft":
            raise ValidationError("Enrollment is no longer editable.")
        # Changing the baseline invalidates the later pose and identity checks.
        e.samples.filter(**({} if pose == "front" else {"pose": pose})).delete()
        e.revision += 1
        e.save()
        e.jobs.filter(status__in=["pending", "processing"]).update(status="cancelled")
        return Response(enrollment_data(e))


@api_view(["GET"])
def sample_file(request, pk):
    admin(request)
    sample = get_object_or_404(Sample.objects.select_related("enrollment"), pk=pk)
    if (
        sample.enrollment.status == "draft"
        and sample.enrollment.owner_id != request.user.id
    ):
        raise PermissionDenied()
    return FileResponse(sample.image.open("rb"), content_type="image/jpeg")


def filtered_events(request):
    qs = events().order_by("-timestamp")
    q = request.query_params.get("q", "")[:120]
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(personnel_id__icontains=q)
            | Q(ble_tag__icontains=q)
        )
    for key in ["outcome", "direction", "rank"]:
        value = request.query_params.get(key)
        if value:
            qs = qs.filter(**{key: value[:60]})
    for key, lookup in [("start", "timestamp__gte"), ("end", "timestamp__lt")]:
        value = request.query_params.get(key)
        if value:
            day = parse_date(value)
            if day is None:
                raise ValidationError("Invalid date range.")
            if key == "end":
                day += timedelta(days=1)
            qs = qs.filter(
                **{lookup: timezone.make_aware(datetime.combine(day, time.min))}
            )
    if request.query_params.get("alerts") == "1":
        qs = qs.exclude(outcome="verified").filter(
            reviewed_at__isnull=True, kind="verification"
        )
    return qs


@api_view(["GET"])
def logs(request):
    admin(request)
    qs = filtered_events(request)
    sort = request.query_params.get("sort", "-timestamp")
    if sort not in [
        "timestamp",
        "-timestamp",
        "name",
        "-name",
        "personnel_id",
        "-personnel_id",
        "direction",
        "-direction",
        "outcome",
        "-outcome",
        "similarity",
        "-similarity",
    ]:
        raise ValidationError("Invalid sort.")
    qs = qs.order_by(sort, "id")
    if request.query_params.get("export") == "1":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "Personnel ID",
                "Name",
                "Position",
                "Unit",
                "BLE tag",
                "Timestamp (UTC)",
                "Direction (operator mode)",
                "Match score",
                "Result",
                "Record type",
                "Review note",
            ]
        )

        def cell(v):
            text = str(v if v is not None else "")
            return (
                "'" + text
                if text.startswith(("=", "+", "-", "@", "\t", "\r"))
                else text
            )

        for e in qs.iterator():
            writer.writerow(
                [
                    cell(x)
                    for x in [
                        e.personnel_id,
                        e.name,
                        e.rank,
                        e.unit,
                        e.ble_tag,
                        e.timestamp.isoformat(),
                        e.direction,
                        e.similarity,
                        e.outcome,
                        e.kind,
                        e.review_note,
                    ]
                ]
            )
        audit(request, "logs.export")
        result = HttpResponse(output.getvalue(), content_type="text/csv")
        result["Content-Disposition"] = 'attachment; filename="verification-logs.csv"'
        return result
    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except ValueError:
        raise ValidationError("Invalid page.")
    return Response(
        {
            "results": VerificationSerializer(
                qs[(page - 1) * 25 : page * 25], many=True
            ).data,
            "total": qs.count(),
            "page": page,
        }
    )


@api_view(["GET"])
def log_detail(request, pk):
    admin(request)
    return Response(VerificationSerializer(get_object_or_404(events(), pk=pk)).data)


@api_view(["GET"])
def log_image(request, pk, kind):
    admin(request)
    if kind not in ["captured", "registered"]:
        return HttpResponse(status=404)
    image = get_object_or_404(Verification.objects.only(kind + "_image"), pk=pk)
    data = getattr(image, kind + "_image")
    return (
        HttpResponse(bytes(data), content_type="image/jpeg")
        if data
        else HttpResponse(status=404)
    )


@api_view(["GET"])
def personnel_photo(request, pk):
    admin(request)
    profile = get_object_or_404(FaceProfile.objects.only("photo"), person_id=pk)
    return (
        HttpResponse(bytes(profile.photo), content_type="image/jpeg")
        if profile.photo
        else HttpResponse(status=404)
    )


@api_view(["POST"])
def review(request, pk):
    admin(request)
    note = request.data.get("note", "")
    if not isinstance(note, str) or not note.strip() or len(note) > 2000:
        raise ValidationError("Enter review notes of 1–2000 characters.")
    e = get_object_or_404(events(), pk=pk)
    if e.outcome == "verified" or e.kind != "verification":
        raise ValidationError("This record does not require review.")
    e.review_note = note.strip()
    e.reviewed_at = timezone.now()
    e.reviewed_by = request.user
    e.save()
    audit(request, "alert.review", e.id, {"note": e.review_note})
    return Response(VerificationSerializer(e).data)


@api_view(["GET"])
def dashboard(request):
    admin(request)
    today = timezone.localdate()
    start = timezone.make_aware(datetime.combine(today, time.min))
    end = start + timedelta(days=1)
    qs = Verification.objects.filter(
        timestamp__gte=start, timestamp__lt=end, kind="verification"
    )
    station = Station.objects.first()
    online = bool(
        station
        and station.heartbeat
        and timezone.now() - station.heartbeat < timedelta(seconds=15)
    )
    return Response(
        dict(
            entries=qs.filter(direction="entry").count(),
            exits=qs.filter(direction="exit").count(),
            occupancy=None,
            alerts=Verification.objects.exclude(outcome="verified")
            .filter(reviewed_at__isnull=True, kind="verification")
            .count(),
            latest=VerificationSerializer(
                events().order_by("-timestamp")[:1], many=True
            ).data,
            recent=VerificationSerializer(
                events().order_by("-timestamp")[:5], many=True
            ).data,
            online=online,
            station={
                "heartbeat": station.heartbeat,
                "health": station.health,
                "gallery_version": station.gallery_version,
            }
            if station
            else None,
            gallery_version=gallery()["version"],
            people=PersonnelSerializer(
                Personnel.objects.order_by("-created_at")[:3], many=True
            ).data,
        )
    )


class ServiceAuthentication(BaseAuthentication):
    def authenticate(self, request):
        if request.META.get("REMOTE_ADDR") not in ["127.0.0.1", "::1"]:
            raise AuthenticationFailed("Local service only.")
        value = request.headers.get("Authorization", "")
        if not settings.SERVICE_TOKEN or not secrets.compare_digest(
            value, "Bearer " + settings.SERVICE_TOKEN
        ):
            raise AuthenticationFailed("Invalid service credential.")
        return (None, True)


def service_view(methods):
    def wrap(fn):
        return api_view(methods)(
            authentication_classes([ServiceAuthentication])(
                permission_classes([AllowAny])(throttle_classes([])(fn))
            )
        )

    return wrap


@service_view(["GET"])
def service_gallery(request):
    return Response(gallery())


@service_view(["GET"])
def service_photo(request, pk, version):
    profile = get_object_or_404(
        FaceProfile, person_id=pk, version=version, person__status="active"
    )
    return (
        HttpResponse(bytes(profile.photo), content_type="image/jpeg")
        if profile.photo
        else HttpResponse(status=404)
    )


@service_view(["POST"])
def heartbeat(request):
    station, _ = Station.objects.get_or_create(pk=1)
    station.heartbeat = timezone.now()
    station.health = request.data.get("health", {})
    station.gallery_version = str(request.data.get("gallery_version", ""))[:100]
    station.save()
    return Response({"direction": station.direction})


@service_view(["POST"])
def ingest(request):
    d = request.data
    try:
        ident = uuid.UUID(d["id"])
        stamp = parse_datetime(d["timestamp"])
        if stamp is None or timezone.is_naive(stamp):
            raise ValueError()
        if d["direction"] not in ["entry", "exit"] or d["outcome"] not in OUTCOMES:
            raise ValueError()
        score = d.get("similarity")
        if score is not None and (
            not isinstance(score, (int, float))
            or not math.isfinite(score)
            or not -1 <= score <= 1
        ):
            raise ValueError()
        evidence = d.get("evidence", {})
        if not isinstance(evidence, dict) or len(json.dumps(evidence)) > 8000:
            raise ValueError()
        p = (
            Personnel.objects.filter(pk=d.get("person_id")).first()
            if d.get("person_id")
            else None
        )
    except (ValueError, KeyError, TypeError):
        raise ValidationError("Invalid verification event.")
    snapshot = d.get("snapshot", {})
    limits = {"name": 120, "personnel_id": 60, "rank": 60, "unit": 100, "ble_tag": 120}
    if not isinstance(snapshot, dict) or any(
        not isinstance(snapshot.get(k, ""), str) or len(snapshot.get(k, "")) > n
        for k, n in limits.items()
    ):
        raise ValidationError("Invalid identity snapshot.")
    try:
        captured = decode_image(d.get("captured_image"))
        registered = decode_image(d.get("registered_image"))
        captured_at = (
            parse_datetime(d["captured_at"])
            if d.get("captured_at")
            else (stamp if captured else None)
        )
        registered_at = (
            parse_datetime(d["registered_at"]) if d.get("registered_at") else None
        )
        if (captured and captured_at is None) or any(
            t is not None and timezone.is_naive(t) for t in [captured_at, registered_at]
        ):
            raise ValueError("Image timestamps must include a timezone.")
        if d.get("registered_at") and registered_at is None:
            raise ValueError("Invalid registration timestamp.")
        if registered and p is None:
            raise ValueError("A registration photo requires a claimed identity.")
        # Older/offline senders may omit the reference photo. Only use the DB
        # photo when the event explicitly names the same immutable profile version.
        if (
            registered is None
            and p
            and hasattr(p, "profile")
            and str(p.profile.version) == str(evidence.get("profile_version"))
            and p.profile.photo
        ):
            registered = bytes(p.profile.photo)
            registered_at = p.profile.photo_taken_at
    except (ValueError, TypeError) as exc:
        raise ValidationError(str(exc))
    defaults = dict(
        captured_image=captured,
        registered_image=registered,
        captured_at=captured_at,
        registered_at=registered_at,
        timestamp=stamp,
        direction=d["direction"],
        outcome=d["outcome"],
        similarity=score,
        evidence=evidence,
        person=p,
        name=snapshot.get("name", p.name if p else "Unidentified"),
        personnel_id=snapshot.get("personnel_id", p.personnel_id if p else ""),
        rank=snapshot.get("rank", p.rank if p else ""),
        unit=snapshot.get("unit", p.unit if p else ""),
        ble_tag=snapshot.get("ble_tag", (p.ble_tag or "") if p else ""),
    )
    event, created = Verification.objects.get_or_create(id=ident, defaults=defaults)
    return Response({"id": str(event.id), "created": created})


@service_view(["POST"])
def claim_job(request):
    with transaction.atomic():
        ProcessingJob.objects.filter(
            status="processing", leased_at__lt=timezone.now() - timedelta(seconds=60)
        ).update(status="pending", lease=None)
        job = (
            ProcessingJob.objects.select_for_update(skip_locked=True)
            .filter(status="pending")
            .order_by("created_at")
            .first()
        )
        if not job:
            return Response({"job": None})
        job.status = "processing"
        job.lease = uuid.uuid4()
        job.leased_at = timezone.now()
        job.save()
        samples = list(job.enrollment.samples.order_by("created_at"))
        fronts = [s for s in samples if s.pose == "front"]
        baseline = (
            [
                sum(s.quality.get(k, 0) for s in fronts) / len(fronts)
                for k in ["yaw", "pitch"]
            ]
            if fronts
            else [0, 0.5]
        )
        return Response(
            {
                "job": dict(
                    id=str(job.id),
                    lease=str(job.lease),
                    pose=job.pose,
                    baseline=baseline,
                    reference=fronts[0].embedding if fronts else None,
                    references=[s.embedding for s in fronts],
                    image_url=f"/api/service/jobs/{job.id}/image/",
                )
            }
        )


@service_view(["GET"])
def job_image(request, pk):
    job = get_object_or_404(ProcessingJob, pk=pk, status="processing")
    return FileResponse(job.image.open("rb"), content_type="image/jpeg")


@service_view(["POST"])
def complete_job(request, pk):
    with transaction.atomic():
        job = get_object_or_404(ProcessingJob.objects.select_for_update(), pk=pk)
        if (
            str(job.lease) != str(request.data.get("lease"))
            or job.status != "processing"
        ):
            raise ValidationError("Job lease expired.")
        e = Enrollment.objects.select_for_update().get(pk=job.enrollment_id)
        if e.status != "draft" or e.revision != job.revision:
            job.status = "cancelled"
        elif request.data.get("error"):
            job.status = "rejected"
            job.error = str(request.data["error"])[:500]
        elif e.samples.filter(pose=job.pose).count() >= 8:
            job.status = "cancelled"
        else:
            embedding = vector(request.data.get("embedding"))
            quality = request.data.get("quality", {})
            if not isinstance(quality, dict):
                raise ValidationError("Invalid quality metrics.")
            sample = Sample(
                enrollment=e, pose=job.pose, embedding=embedding, quality=quality
            )
            with job.image.open("rb") as source:
                image = source.read()
            bounds = quality.get("crop")
            if bounds is not None:
                import cv2
                import numpy as np

                frame = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
                if (
                    frame is None
                    or not isinstance(bounds, list)
                    or len(bounds) != 4
                    or any(type(n) is not int for n in bounds)
                ):
                    raise ValidationError("Invalid face crop.")
                x1, y1, x2, y2 = bounds
                if not (
                    0 <= x1 < x2 <= frame.shape[1] and 0 <= y1 < y2 <= frame.shape[0]
                ):
                    raise ValidationError("Invalid face crop bounds.")
                ok, encoded = cv2.imencode(
                    ".jpg", frame[y1:y2, x1:x2], [cv2.IMWRITE_JPEG_QUALITY, 95]
                )
                if not ok:
                    raise ValidationError("Could not encode face crop.")
                image = encoded.tobytes()
            sample.image.save(f"{uuid.uuid4()}.jpg", ContentFile(image), save=False)
            sample.save()
            job.status = "accepted"
        job.save()
        # The accepted sample is private and retained; the queued original is disposable.
        job.image.delete(save=False)
        return Response({"status": job.status})


@api_view(["GET", "POST"])
def ble(request):
    admin(request)
    try:
        r = Request(
            "http://127.0.0.1:8766/ble",
            data=b"{}" if request.method == "POST" else None,
            headers={
                "Authorization": "Bearer " + settings.SERVICE_TOKEN,
                "Content-Type": "application/json",
            },
        )
        with urlopen(r, timeout=3) as response:
            return Response(json.load(response))
    except (URLError, TimeoutError, OSError):
        return Response(
            {
                "error": "Recognition service is not running. Start the local services.",
                "tags": [],
                "scanning": False,
            },
            status=503,
        )


@api_view(["GET", "POST"])
def monitor(request):
    if request.META.get("REMOTE_ADDR") not in ["127.0.0.1", "::1"]:
        raise PermissionDenied("Open monitoring on the MacBook itself.")
    if request.method == "POST":
        direction = request.data.get("direction")
        if direction and direction not in ["entry", "exit"]:
            raise ValidationError("Invalid direction.")
        if direction:
            station, _ = Station.objects.get_or_create(pk=1)
            station.direction = direction
            station.save()
            audit(request, "monitor.direction", details={"direction": direction})
        action = request.data.get("action")
        if action not in [None, "start", "stop"]:
            raise ValidationError("Invalid camera action.")
        data = {"action": action}
    else:
        data = {}
    payload = json.dumps(data).encode() if data or request.method == "POST" else None
    try:
        r = Request(
            "http://127.0.0.1:8766/status",
            data=payload,
            headers={
                "Authorization": "Bearer " + settings.SERVICE_TOKEN,
                "Content-Type": "application/json",
            },
        )
        with urlopen(r, timeout=3) as response:
            result = json.load(response)
        return Response(result)
    except (URLError, TimeoutError, OSError):
        return Response(
            {
                "running": False,
                "error": "Recognition service is not running. Start the local services.",
            },
            status=503,
        )


@api_view(["GET"])
def monitor_image(request, kind):
    if request.META.get("REMOTE_ADDR") not in ["127.0.0.1", "::1"]:
        raise PermissionDenied("Local monitor only.")
    if kind not in ["captured", "registered"]:
        return HttpResponse(status=404)
    try:
        event_id = uuid.UUID(request.query_params.get("event_id", ""))
    except (ValueError, TypeError):
        raise ValidationError("Invalid event identifier.")
    try:
        with urlopen(
            Request(
                f"http://127.0.0.1:8766/latest/{kind}/{event_id}",
                headers={"Authorization": "Bearer " + settings.SERVICE_TOKEN},
            ),
            timeout=3,
        ) as response:
            return HttpResponse(
                response.read(512 * 1024 + 1), content_type="image/jpeg"
            )
    except (URLError, TimeoutError, OSError):
        return HttpResponse(status=404)


@api_view(["GET"])
def monitor_frame(request):
    if request.META.get("REMOTE_ADDR") not in ["127.0.0.1", "::1"]:
        raise PermissionDenied("Local preview only.")
    try:
        r = Request(
            "http://127.0.0.1:8766/frame",
            headers={"Authorization": "Bearer " + settings.SERVICE_TOKEN},
        )
        with urlopen(r, timeout=3) as response:
            data = response.read(2 * 1024 * 1024)
        return HttpResponse(data, content_type="image/jpeg")
    except (URLError, TimeoutError, OSError):
        return HttpResponse(status=503)


def _monitor_connection(request, path, content_type):
    # Streaming responses do not use DRF JSON content negotiation. Browsers send
    # image/* or text/event-stream here, which JSON-only negotiation rejects.
    if not request.user.is_authenticated or request.META.get("REMOTE_ADDR") not in [
        "127.0.0.1",
        "::1",
    ]:
        return HttpResponse(status=403)
    if path == "stream" and request.GET.get("session"):
        try:
            session = uuid.UUID(request.GET["session"]).hex
        except (ValueError, TypeError, AttributeError):
            return HttpResponse(status=400)
        path += "?session=" + session
    try:
        upstream = urlopen(
            Request(
                "http://127.0.0.1:8766/" + path,
                headers={"Authorization": "Bearer " + settings.SERVICE_TOKEN},
            ),
            timeout=5,
        )
    except (URLError, TimeoutError, OSError):
        return HttpResponse(status=503)

    def chunks():
        try:
            while True:
                block = upstream.read1(64 * 1024)
                if not block:
                    break
                yield block
        except (TimeoutError, OSError):
            pass
        finally:
            upstream.close()

    response = StreamingHttpResponse(chunks(), content_type=content_type)
    response["X-Accel-Buffering"] = "no"
    return response


@require_http_methods(["GET"])
def monitor_stream(request):
    return _monitor_connection(
        request, "stream", "multipart/x-mixed-replace; boundary=frame"
    )


@require_http_methods(["GET"])
def monitor_presence(request):
    return _monitor_connection(request, "presence", "text/event-stream")


def index(request, pk=None):
    path = settings.ROOT / "frontend" / "dist" / "index.html"
    if not path.exists():
        return HttpResponse("Build the frontend first.", status=503)
    return FileResponse(path.open("rb"), content_type="text/html")


@api_view(["POST"])
def password(request):
    from django.contrib.auth import update_session_auth_hash
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError as DjangoValidationError

    old = request.data.get("old_password", "")
    new = request.data.get("password", "")
    if (
        not isinstance(old, str)
        or not isinstance(new, str)
        or not request.user.check_password(old)
    ):
        raise ValidationError("Current password is incorrect.")
    try:
        validate_password(new, request.user)
    except DjangoValidationError as exc:
        raise ValidationError(exc.messages)
    request.user.set_password(new)
    request.user.save()
    update_session_auth_hash(request, request.user)
    audit(request, "password.change")
    return Response({"ok": True, "csrf": get_token(request)})
