"""Import existing registrations as incomplete records without changing source files."""

import json
import uuid

import numpy as np
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.dateparse import parse_datetime

from access.models import FaceProfile, Personnel, Verification
from face_models import MODEL_ID, PROFILE_NAME


class Command(BaseCommand):
    help = "Import legacy registrations and face sightings. Originals remain unchanged; personnel require review."

    def handle(self, *args, **options):
        root = settings.ROOT
        for folder in sorted((root / "registration").glob("person*")):
            if folder.is_symlink() or not folder.is_dir():
                continue
            try:
                profile_path = folder / PROFILE_NAME
                metadata_path = folder / "metadata.json"
                if profile_path.is_symlink() or metadata_path.is_symlink():
                    raise ValueError("Linked files are unsupported")
                metadata = json.loads(metadata_path.read_text())
                with np.load(profile_path, allow_pickle=False) as f:
                    features = f["features"].astype(np.float32)
                    if (
                        str(f["model_id"].item()) != MODEL_ID
                        or features.shape != (40, 512)
                        or not np.isfinite(features).all()
                    ):
                        raise ValueError("Incompatible profile")
                    norms = np.linalg.norm(features, axis=1, keepdims=True)
                    if np.any(norms < 1e-8):
                        raise ValueError("Invalid embeddings")
                    features = features / norms
                    reference = features.mean(0)
                    norm = np.linalg.norm(reference)
                    if norm < 1e-8:
                        raise ValueError("Invalid reference")
                    reference /= norm
                with transaction.atomic():
                    p, created = Personnel.objects.get_or_create(
                        legacy_folder=folder.name,
                        defaults=dict(
                            personnel_id="LEGACY-" + folder.name,
                            name=metadata.get("name", folder.name),
                            status="incomplete",
                        ),
                    )
                    if created:
                        FaceProfile.objects.create(
                            person=p,
                            model_id=MODEL_ID,
                            reference=reference.tolist(),
                            features=features.tolist(),
                        )
                self.stdout.write(
                    f"{folder.name}: "
                    + ("imported; review required" if created else "already imported")
                )
            except (ValueError, OSError, KeyError, TypeError) as exc:
                self.stderr.write(f"{folder.name}: skipped ({exc})")
        path = root / "data" / "entries.jsonl"
        if path.exists():
            for n, line in enumerate(path.read_text().splitlines()):
                try:
                    data = json.loads(line)
                    stamp = parse_datetime(data["timestamp"])
                    if stamp is None:
                        raise ValueError("Missing timestamp")
                    p = Personnel.objects.filter(
                        legacy_folder=data.get("person_id")
                    ).first()
                    ident = uuid.uuid5(uuid.NAMESPACE_URL, "pnp-legacy:" + line)
                    Verification.objects.get_or_create(
                        id=ident,
                        defaults=dict(
                            person=p,
                            name=data.get("name", "Unidentified"),
                            timestamp=stamp,
                            direction="",
                            direction_source="unknown",
                            outcome="legacy_sighting",
                            kind="legacy_sighting",
                            similarity=data.get("similarity"),
                        ),
                    )
                except (ValueError, KeyError, TypeError):
                    self.stderr.write(f"Log line {n + 1}: skipped")
