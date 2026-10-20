from django.core.management.base import BaseCommand, CommandError

from access.models import Personnel


class Command(BaseCommand):
    help = "Activate a reviewed imported profile after official fields and BLE assignment are completed."

    def add_arguments(self, parser):
        parser.add_argument("personnel_id")

    def handle(self, *args, **options):
        try:
            p = Personnel.objects.get(personnel_id=options["personnel_id"])
        except Personnel.DoesNotExist:
            raise CommandError("Personnel not found.")
        if (
            not p.legacy_folder
            or not hasattr(p, "profile")
            or not all([p.rank, p.unit, p.ble_tag])
            or p.personnel_id.startswith("LEGACY-")
        ):
            raise CommandError("Review official ID, rank, unit, and BLE tag first.")
        p.status = "active"
        p.save()
        self.stdout.write("Imported profile activated.")
