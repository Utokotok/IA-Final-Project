#!/usr/bin/env python3
"""Cross-platform setup for PNP Access Control (macOS, Windows, Linux)."""

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
RUNTIME.mkdir(mode=0o700, exist_ok=True)
if hasattr(os, "umask"):
    os.umask(0o077)


def get_python_exe() -> str:
    """Return path to virtual environment python if it exists, otherwise current executable."""
    if sys.platform == "win32":
        venv_py = ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        venv_py = ROOT / ".venv" / "bin" / "python"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable


def find_tool(name: str, macos_path: str | None = None) -> Path | None:
    """Find a binary on the system (checking Homebrew path on macOS first)."""
    if macos_path and Path(macos_path).exists():
        return Path(macos_path)
    found = shutil.which(name)
    return Path(found) if found else None


def find_postgres() -> Path | None:
    """Find directory containing initdb and pg_ctl, if installed."""
    brew_pg = Path("/opt/homebrew/opt/postgresql@17/bin")
    if (brew_pg / "initdb").exists() and (brew_pg / "pg_ctl").exists():
        return brew_pg
    initdb = shutil.which("initdb")
    pg_ctl = shutil.which("pg_ctl")
    if initdb and pg_ctl:
        return Path(initdb).parent
    return None


def discover_hosts() -> list[str]:
    """Discover hostnames and local IP addresses."""
    hosts = {"localhost", "127.0.0.1", socket.gethostname().lower()}
    if sys.platform == "darwin":
        for interface in ["en0", "en1"]:
            result = subprocess.run(
                ["/usr/sbin/ipconfig", "getifaddr", interface],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                hosts.add(result.stdout.strip())
    else:
        try:
            _, _, ips = socket.gethostbyname_ex(socket.gethostname())
            for ip in ips:
                if not ip.startswith("127."):
                    hosts.add(ip)
        except Exception:
            pass
    return sorted(hosts)


# 1. Configuration
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
config["hosts"] = discover_hosts()

pg_bin = find_postgres()
caddy_bin = find_tool("caddy", "/opt/homebrew/bin/caddy")
mkcert_bin = find_tool("mkcert", "/opt/homebrew/bin/mkcert")

if pg_bin:
    config["database_engine"] = "postgresql"
    print(f"PostgreSQL detected at {pg_bin}")
else:
    config["database_engine"] = "sqlite3"
    print("PostgreSQL not found. Using SQLite (runtime/db.sqlite3).")

use_https = bool(caddy_bin and mkcert_bin)
config["https"] = use_https
if not use_https:
    print("Caddy or mkcert not detected. Running via direct HTTP.")

config_path.write_text(json.dumps(config, indent=2))
try:
    config_path.chmod(0o600)
except OSError:
    pass

# 2. Database Initialization
if pg_bin:
    pgdata = RUNTIME / "postgres"
    pwfile = RUNTIME / ".pg-password"
    pwfile.write_text(config["pg_password"])
    try:
        pwfile.chmod(0o600)
    except OSError:
        pass

    if not (pgdata / "PG_VERSION").exists():
        subprocess.run(
            [
                str(pg_bin / "initdb"),
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
        [str(pg_bin / "pg_ctl"), "-D", str(pgdata), "status"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode:
        subprocess.run(
            [
                str(pg_bin / "pg_ctl"),
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

# 3. Migrations and Static Collection
python_exe = get_python_exe()
backend = ROOT / "backend"
for command in [["migrate", "--noinput"], ["collectstatic", "--noinput"]]:
    subprocess.run(
        [python_exe, str(backend / "manage.py"), *command], check=True, cwd=ROOT
    )

if config.get("database_engine") == "sqlite3":
    import sqlite3
    db_file = RUNTIME / "db.sqlite3"
    if db_file.exists():
        with sqlite3.connect(str(db_file)) as con:
            con.execute("PRAGMA journal_mode=WAL;")
            con.execute("PRAGMA busy_timeout=30000;")

# 4. Superuser Setup
sys.path.insert(0, str(backend))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()
from django.contrib.auth import get_user_model

User = get_user_model()
if not User.objects.filter(is_superuser=True).exists():
    password = secrets.token_urlsafe(20)
    User.objects.create_superuser(username="admin", password=password)
    login_info = (
        f"Username: admin\nPassword: {password}\n\n"
        "Change this password in the dashboard after first login, then remove this file.\n"
    )
    (RUNTIME / "initial-login.txt").write_text(login_info)
    try:
        (RUNTIME / "initial-login.txt").chmod(0o600)
    except OSError:
        pass
    print("\nInitial admin account created:")
    print(f"  Username: admin")
    print(f"  Password: {password}")
    print("  Saved to: runtime/initial-login.txt")

# 5. HTTPS Setup (if Caddy and mkcert are available)
if use_https:
    ca = RUNTIME / "ca"
    ca.mkdir(exist_ok=True)
    env = {**os.environ, "CAROOT": str(ca)}
    subprocess.run(
        [
            str(mkcert_bin),
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
    print("\nHTTPS certificate generated. Trust runtime/ca/rootCA.pem on devices.")

print("\nSetup complete! Run 'python scripts/start.py' to start the application.")
