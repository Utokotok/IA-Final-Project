#!/usr/bin/env python3
"""Validate a trusted local backup by restoring it into a disposable database."""

import argparse
import json
import os
import subprocess
import tarfile
import tempfile
import uuid
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("archive", type=Path)
args = parser.parse_args()
config = json.loads((ROOT / "runtime/config.json").read_text())
name = "pnp_restore_" + uuid.uuid4().hex[:12]
connection = dict(
    host="127.0.0.1",
    port=config["db_port"],
    dbname="postgres",
    user="pnp_owner",
    password=config["pg_password"],
    autocommit=True,
)
with tempfile.TemporaryDirectory() as temp, psycopg.connect(**connection) as owner:
    with tarfile.open(args.archive, "r:gz") as archive:
        archive.extractall(temp, filter="data")
    owner.execute(
        sql.SQL("CREATE DATABASE {} OWNER {}").format(
            sql.Identifier(name), sql.Identifier(config["db_user"])
        )
    )
    try:
        subprocess.run(
            [
                "/opt/homebrew/opt/postgresql@17/bin/pg_restore",
                "--exit-on-error",
                "--no-owner",
                "-h",
                "127.0.0.1",
                "-p",
                str(config["db_port"]),
                "-U",
                config["db_user"],
                "-d",
                name,
                str(Path(temp) / "database.dump"),
            ],
            env={**os.environ, "PGPASSWORD": config["db_password"]},
            check=True,
        )
        with psycopg.connect(
            host="127.0.0.1",
            port=config["db_port"],
            dbname=name,
            user=config["db_user"],
            password=config["db_password"],
        ) as restored:
            count = restored.execute(
                "SELECT COUNT(*) FROM access_personnel"
            ).fetchone()[0]
            for (relative,) in restored.execute("SELECT image FROM access_sample"):
                target = (Path(temp) / "media" / relative).resolve()
                if (
                    not target.is_relative_to(Path(temp).resolve())
                    or not target.is_file()
                ):
                    raise RuntimeError("Backup is missing an enrollment image.")
            print(f"Restore verified: {count} personnel; linked sample files checked.")
    finally:
        owner.execute(
            sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
        )
