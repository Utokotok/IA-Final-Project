#!/usr/bin/env python3
"""Create an isolated PostgreSQL cluster and private LAN configuration."""

import json
import os
import secrets
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
RUNTIME.mkdir(mode=0o700, exist_ok=True)
os.umask(0o077)
PG = Path("/opt/homebrew/opt/postgresql@17/bin")
if not (PG / "initdb").exists():
    raise SystemExit("Install PostgreSQL 17, Caddy, and mkcert first. See SETUP.md.")
config_path = RUNTIME / "config.json"
config = (
    json.loads(config_path.read_text())
    if config_path.exists()
    else dict(
        secret_key=secrets.token_urlsafe(64),
        service_token=secrets.token_urlsafe(48),
        database="pnp",
        db_user="pnp",
        db_password=secrets.token_urlsafe(40),
        db_port=55432,
        pg_password=secrets.token_urlsafe(40),
    )
)
hosts = {"localhost", "127.0.0.1", socket.gethostname().lower()}
for interface in ["en0", "en1"]:
    result = subprocess.run(
        ["/usr/sbin/ipconfig", "getifaddr", interface], capture_output=True, text=True
    )
    if result.returncode == 0:
        hosts.add(result.stdout.strip())
config["hosts"] = sorted(hosts)
config_path.write_text(json.dumps(config, indent=2))
config_path.chmod(0o600)
pgdata = RUNTIME / "postgres"
pwfile = RUNTIME / ".pg-password"
pwfile.write_text(config["pg_password"])
pwfile.chmod(0o600)
if not (pgdata / "PG_VERSION").exists():
    subprocess.run(
        [
            str(PG / "initdb"),
            "-D",
            str(pgdata),
            "-U",
            "pnp_owner",
            "--pwfile",
            str(pwfile),
            "--auth-local=scram-sha-256",
            "--auth-host=scram-sha-256",
            "--encoding=UTF8",
            "--locale=C",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    with (pgdata / "postgresql.conf").open("a") as f:
        f.write(
            "\nlisten_addresses = '127.0.0.1'\nport = 55432\nunix_socket_directories = ''\n"
        )
if subprocess.run(
    [str(PG / "pg_ctl"), "-D", str(pgdata), "status"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
).returncode:
    subprocess.run(
        [
            str(PG / "pg_ctl"),
            "-D",
            str(pgdata),
            "-l",
            str(RUNTIME / "postgres.log"),
            "start",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
import psycopg
from psycopg import sql

with psycopg.connect(
    host="127.0.0.1",
    port=config["db_port"],
    dbname="postgres",
    user="pnp_owner",
    password=config["pg_password"],
    autocommit=True,
) as db:
    if not db.execute(
        "SELECT 1 FROM pg_roles WHERE rolname=%s", (config["db_user"],)
    ).fetchone():
        db.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(config["db_user"]), sql.Literal(config["db_password"])
            )
        )
    if not db.execute(
        "SELECT 1 FROM pg_database WHERE datname=%s", (config["database"],)
    ).fetchone():
        db.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(config["database"]), sql.Identifier(config["db_user"])
            )
        )
backend = ROOT / "backend"
for command in [["migrate", "--noinput"], ["collectstatic", "--noinput"]]:
    subprocess.run(
        [sys.executable, str(backend / "manage.py"), *command], check=True, cwd=ROOT
    )
sys.path.insert(0, str(backend))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()
from django.contrib.auth import get_user_model

if not get_user_model().objects.filter(is_superuser=True).exists():
    password = secrets.token_urlsafe(20)
    get_user_model().objects.create_superuser(username="admin", password=password)
    (RUNTIME / "initial-login.txt").write_text(
        "Username: admin\nPassword: "
        + password
        + "\n\nChange this password in the dashboard after first login, then remove this file.\n"
    )
    (RUNTIME / "initial-login.txt").chmod(0o600)
ca = RUNTIME / "ca"
ca.mkdir(exist_ok=True)
env = {**os.environ, "CAROOT": str(ca)}
subprocess.run(
    [
        "/opt/homebrew/bin/mkcert",
        "-cert-file",
        str(RUNTIME / "lan.pem"),
        "-key-file",
        str(RUNTIME / "lan-key.pem"),
        *config["hosts"],
    ],
    env=env,
    check=True,
)
addresses = ", ".join("https://" + h + ":8443" for h in config["hosts"])
(RUNTIME / "Caddyfile").write_text(
    """{
    auto_https disable_redirects
    admin off
}
"""
    + addresses
    + ''' {
    tls "'''
    + str(RUNTIME / "lan.pem")
    + '" "'
    + str(RUNTIME / "lan-key.pem")
    + """"
    reverse_proxy 127.0.0.1:8000
}
"""
)
print(
    "Local setup complete. Credentials are in runtime/initial-login.txt (not printed)."
)
print(
    "Trust runtime/ca/rootCA.pem on participating devices; never share rootCA-key.pem."
)
print("Run scripts/start.py to start the web application and recognition service.")
