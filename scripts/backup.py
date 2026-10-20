#!/usr/bin/env python3
"""Run with application services stopped for a consistent DB/files backup."""

import argparse
import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
runtime = ROOT / "runtime"
parser = argparse.ArgumentParser()
parser.add_argument("destination", type=Path)
args = parser.parse_args()
for name in ["backend", "recognition"]:
    file = runtime / (name + ".pid")
    if file.exists():
        try:
            os.kill(int(file.read_text()), 0)
        except (OSError, ValueError):
            pass
        else:
            raise SystemExit(
                "Stop application services before making a consistent backup."
            )
config = json.loads((runtime / "config.json").read_text())
os.umask(0o077)
with tempfile.TemporaryDirectory() as temp:
    dump = Path(temp) / "database.dump"
    subprocess.run(
        [
            "/opt/homebrew/opt/postgresql@17/bin/pg_dump",
            "-h",
            "127.0.0.1",
            "-p",
            str(config["db_port"]),
            "-U",
            config["db_user"],
            "-Fc",
            "-f",
            str(dump),
            config["database"],
        ],
        env={**os.environ, "PGPASSWORD": config["db_password"]},
        check=True,
    )
    with tarfile.open(args.destination, "x:gz") as archive:
        archive.add(dump, arcname="database.dump")
        for name in ["media", "gallery.json", "reference_photos", "outbox.sqlite3"]:
            if (runtime / name).exists():
                archive.add(runtime / name, arcname=name)
print("Backup created. It contains private biometric data; keep it protected.")
