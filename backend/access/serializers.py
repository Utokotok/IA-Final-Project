from django.utils import timezone
from rest_framework import serializers

from .models import FaceProfile, Personnel, Verification


class PersonnelSerializer(serializers.ModelSerializer):
    has_profile = serializers.SerializerMethodField()
    thumbnail = serializers.SerializerMethodField()

    class Meta:
        model = Personnel
        fields = [
            "id",
            "personnel_id",
            "name",
            "rank",
            "unit",
            "ble_tag",
            "status",
            "created_at",
            "legacy_folder",
            "has_profile",
            "thumbnail",
        ]
        read_only_fields = ["status", "created_at", "legacy_folder"]

    def get_has_profile(self, obj):
        return hasattr(obj, "profile")

    def get_thumbnail(self, obj):
        if FaceProfile.objects.filter(person=obj, photo__isnull=False).exists():
            return f"/api/people/{obj.id}/photo/"
        sample = (
            obj.enrollment.samples.filter(pose="front").first()
            if hasattr(obj, "enrollment")
            else None
        )
        return f"/api/samples/{sample.id}/" if sample else None

    def validate_ble_tag(self, value):
        value = value.strip().lower() if value and value.strip() else None
        qs = (
            Personnel.objects.filter(ble_tag=value)
            if value
            else Personnel.objects.none()
        )
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                "This BLE identifier is already assigned."
            )
        return value

    def validate(self, attrs):
        for k in ["name", "personnel_id", "rank", "unit"]:
            if k in attrs and not attrs[k].strip():
                raise serializers.ValidationError({k: "This field is required."})
        return attrs


class VerificationSerializer(serializers.ModelSerializer):
    captured_image_url = serializers.SerializerMethodField()
    registered_image_url = serializers.SerializerMethodField()
    log_reference = serializers.SerializerMethodField()

    def image_url(self, obj, kind):
        available = getattr(obj, kind + "_image_available", None)
        if available is None:
            available = Verification.objects.filter(
                pk=obj.pk, **{kind + "_image__isnull": False}
            ).exists()
        return f"/api/logs/{obj.id}/images/{kind}/" if available else None

    def get_captured_image_url(self, obj):
        return self.image_url(obj, "captured")

    def get_registered_image_url(self, obj):
        return self.image_url(obj, "registered")

    def get_log_reference(self, obj):
        return (
            "LOG-"
            + timezone.localtime(obj.timestamp).strftime("%Y%m%d-%H%M%S")
            + "-"
            + obj.id.hex[:6].upper()
        )

    class Meta:
        model = Verification
        fields = [
            "id",
            "name",
            "personnel_id",
            "rank",
            "unit",
            "ble_tag",
            "timestamp",
            "received_at",
            "direction",
            "direction_source",
            "outcome",
            "similarity",
            "evidence",
            "kind",
            "reviewed_at",
            "review_note",
            "captured_image_url",
            "registered_image_url",
            "captured_at",
            "registered_at",
            "log_reference",
        ]
