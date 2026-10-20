import uuid

from django.conf import settings
from django.db import models


class Personnel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    personnel_id = models.CharField(max_length=60, unique=True)
    name = models.CharField(max_length=120)
    rank = models.CharField(max_length=60, blank=True)
    unit = models.CharField(max_length=100, blank=True)
    ble_tag = models.CharField(max_length=120, unique=True, null=True, blank=True)
    status = models.CharField(max_length=20, default="incomplete")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    legacy_folder = models.CharField(max_length=100, unique=True, null=True)


class FaceProfile(models.Model):
    person = models.OneToOneField(
        Personnel, on_delete=models.PROTECT, related_name="profile"
    )
    photo = models.BinaryField(null=True, blank=True, editable=False)
    photo_taken_at = models.DateTimeField(null=True, blank=True)
    model_id = models.CharField(max_length=100)
    reference = models.JSONField(default=list)
    features = models.JSONField(default=list)
    version = models.UUIDField(default=uuid.uuid4)
    updated_at = models.DateTimeField(auto_now=True)


class Enrollment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    person = models.OneToOneField(
        Personnel, on_delete=models.PROTECT, related_name="enrollment"
    )
    status = models.CharField(max_length=20, default="draft")
    revision = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class Sample(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    enrollment = models.ForeignKey(
        Enrollment, on_delete=models.CASCADE, related_name="samples"
    )
    pose = models.CharField(max_length=10)
    image = models.FileField(upload_to="samples/%Y/%m/")
    embedding = models.JSONField()
    quality = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class ProcessingJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    enrollment = models.ForeignKey(
        Enrollment, on_delete=models.CASCADE, related_name="jobs"
    )
    image = models.FileField(upload_to="jobs/%Y/%m/")
    pose = models.CharField(max_length=10)
    revision = models.PositiveIntegerField()
    status = models.CharField(max_length=20, default="pending")
    error = models.CharField(max_length=500, blank=True)
    lease = models.UUIDField(null=True)
    leased_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Verification(models.Model):
    captured_image = models.BinaryField(null=True, blank=True, editable=False)
    registered_image = models.BinaryField(null=True, blank=True, editable=False)
    captured_at = models.DateTimeField(null=True, blank=True)
    registered_at = models.DateTimeField(null=True, blank=True)
    id = models.UUIDField(primary_key=True)
    person = models.ForeignKey(Personnel, null=True, on_delete=models.PROTECT)
    name = models.CharField(max_length=120, default="Unidentified")
    personnel_id = models.CharField(max_length=60, blank=True)
    rank = models.CharField(max_length=60, blank=True)
    unit = models.CharField(max_length=100, blank=True)
    ble_tag = models.CharField(max_length=120, blank=True)
    timestamp = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    direction = models.CharField(max_length=10)
    direction_source = models.CharField(max_length=30, default="operator_mode")
    outcome = models.CharField(max_length=30)
    similarity = models.FloatField(null=True)
    evidence = models.JSONField(default=dict)
    kind = models.CharField(max_length=30, default="verification")
    reviewed_at = models.DateTimeField(null=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT
    )
    review_note = models.TextField(blank=True)


class AuditEvent(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT
    )
    action = models.CharField(max_length=60)
    target = models.CharField(max_length=120, blank=True)
    details = models.JSONField(default=dict)
    timestamp = models.DateTimeField(auto_now_add=True)


class Station(models.Model):
    name = models.CharField(max_length=60, default="Gate 1")
    heartbeat = models.DateTimeField(null=True)
    direction = models.CharField(max_length=10, default="entry")
    gallery_version = models.CharField(max_length=100, blank=True)
    health = models.JSONField(default=dict)
