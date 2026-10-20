#!/usr/bin/env python3
"""Run backend tests against an isolated PostgreSQL database without broadening app privileges."""

import json
import subprocess
import sys
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
config = json.loads((ROOT / "runtime/config.json").read_text())
name = "test_" + config["database"]
with psycopg.connect(
    host="127.0.0.1",
    port=config["db_port"],
    dbname="postgres",
    user="pnp_owner",
    password=config["pg_password"],
    autocommit=True,
) as owner:
    if owner.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone():
        raise SystemExit(
            "Test database already exists. Inspect it before retrying; it will not be overwritten."
        )
    owner.execute(
        sql.SQL("CREATE DATABASE {} OWNER {}").format(
            sql.Identifier(name), sql.Identifier(config["db_user"])
        )
    )
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "backend/manage.py"),
                "test",
                "access",
                "--keepdb",
                "--verbosity",
                "1",
            ],
            cwd=ROOT,
            check=False,
        )
    finally:
        owner.execute(
            sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
        )
raise SystemExit(result.returncode)
