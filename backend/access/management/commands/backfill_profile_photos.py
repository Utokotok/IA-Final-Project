from django.core.management.base import BaseCommand

from access.models import FaceProfile, Verification
from access.photos import registration_photo


class Command(BaseCommand):
    help = "Copy existing registration photos into the database without inventing historic gate captures."

    def handle(self, *args, **options):
        copied = 0
        missing = 0
        for profile in FaceProfile.objects.filter(photo__isnull=True).select_related(
            "person"
        ):
            image, taken = registration_photo(profile.person)
            if image:
                profile.photo = image
                profile.photo_taken_at = taken
                profile.save(update_fields=["photo", "photo_taken_at"])
                copied += 1
            else:
                missing += 1
        restored = 0
        for profile in FaceProfile.objects.filter(photo__isnull=False):
            restored += Verification.objects.filter(
                person_id=profile.person_id,
                registered_image__isnull=True,
                kind="verification",
                evidence__profile_version=str(profile.version),
            ).update(
                registered_image=profile.photo, registered_at=profile.photo_taken_at
            )
        self.stdout.write(
            f"Registration photos copied: {copied}; unavailable: {missing}; version-matched registration snapshots restored: {restored}. Historic captures were not changed."
        )
