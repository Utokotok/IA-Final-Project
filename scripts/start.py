#!/usr/bin/env python3
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
runtime = ROOT / "runtime"
os.umask(0o077)
if not (runtime / "config.json").exists():
    raise SystemExit("Run .venv/bin/python scripts/setup.py first.")
missing = [
    name
    for name in [
        "django",
        "rest_framework",
        "psycopg",
        "cv2",
        "numpy",
        "onnxruntime",
        "bleak",
    ]
    if importlib.util.find_spec(name) is None
]
if missing:
    raise SystemExit(
        "Missing dependencies: "
        + ", ".join(missing)
        + ". Run .venv/bin/python -m pip install -r requirements-revision.txt"
    )
pg = "/opt/homebrew/opt/postgresql@17/bin/pg_ctl"
if subprocess.run(
    [pg, "-D", str(runtime / "postgres"), "status"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
).returncode:
    subprocess.run(
        [
            pg,
            "-D",
            str(runtime / "postgres"),
            "-l",
            str(runtime / "postgres.log"),
            "start",
        ],
        check=True,
    )
for name in ["backend", "recognition", "https"]:
    pidfile = runtime / (name + ".pid")
    if pidfile.exists():
        try:
            os.kill(int(pidfile.read_text()), 0)
        except (OSError, ValueError):
            pidfile.unlink(missing_ok=True)
        else:
            raise SystemExit(f"{name} already running. Use scripts/stop.py first.")
env = {**os.environ, "PYTHONUNBUFFERED": "1"}
commands = {
    "backend": [
        str(ROOT / ".venv/bin/waitress-serve"),
        "--listen=127.0.0.1:8000",
        "--threads=16",
        "--trusted-proxy=127.0.0.1",
        "--trusted-proxy-headers=x-forwarded-for x-forwarded-proto",
        "config.wsgi:application",
    ],
    "recognition": [str(ROOT / ".venv/bin/python"), str(ROOT / "revision/worker.py")],
    "https": [
        "/opt/homebrew/bin/caddy",
        "run",
        "--config",
        str(runtime / "Caddyfile"),
        "--adapter",
        "caddyfile",
    ],
}
started = []
try:
    for name, command in commands.items():
        log = (runtime / (name + ".log")).open("ab")
        proc = subprocess.Popen(
            command,
            cwd=ROOT / "backend" if name == "backend" else ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log.close()
        (runtime / (name + ".pid")).write_text(str(proc.pid))
        started.append((name, proc))
        time.sleep(0.8)
        if proc.poll() is not None:
            raise RuntimeError(f"{name} failed; see runtime/{name}.log")
except BaseException:
    for name, p in started:
        if p.poll() is None:
            p.terminate()
        (runtime / (name + ".pid")).unlink(missing_ok=True)
    raise
config = json.loads((runtime / "config.json").read_text())
print("Local monitor: https://localhost:8443/monitor")
print("Admin dashboard addresses:")
for host in config["hosts"]:
    print("  https://" + host + ":8443/")
print("Services started. The camera remains off until started in the monitor.")
