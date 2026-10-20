from django.conf import settings
from django.db.models import BooleanField, Case, Value, When

from revision.photos import normalize_image

from .models import Sample, Verification


def events():
    return Verification.objects.annotate(
        captured_image_available=Case(
            When(captured_image__isnull=False, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
        registered_image_available=Case(
            When(registered_image__isnull=False, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
    ).defer("captured_image", "registered_image")


def registration_photo(person):
    sample = (
        Sample.objects.filter(enrollment__person=person, pose="front")
        .order_by("created_at")
        .first()
    )
    if sample:
        try:
            with sample.image.open("rb") as source:
                return normalize_image(
                    source.read(2 * 1024 * 1024 + 1)
                ), sample.created_at
        except (OSError, ValueError):
            pass
    if person.legacy_folder:
        root = (settings.ROOT / "registration").resolve()
        folder = (root / person.legacy_folder).resolve()
        if folder.parent == root and folder.is_dir():
            for path in sorted(folder.rglob("front*.jpg")):
                if path.is_symlink() or not path.resolve().is_relative_to(folder):
                    continue
                try:
                    return normalize_image(path.read_bytes()), None
                except (OSError, ValueError):
                    continue
    return None, None
