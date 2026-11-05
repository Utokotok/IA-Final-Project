#!/usr/bin/env python3
"""Cross-platform service manager for PNP Access Control (macOS, Windows, Linux)."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
BACKEND = ROOT / "backend"

if hasattr(os, "umask"):
    os.umask(0o077)

config_path = RUNTIME / "config.json"
if not config_path.exists():
    raise SystemExit("Configuration missing. Run 'python scripts/setup.py' first.")

config = json.loads(config_path.read_text())


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
    if macos_path and Path(macos_path).exists():
        return Path(macos_path)
    found = shutil.which(name)
    return Path(found) if found else None


def is_pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
        )
        return str(pid) in result.stdout
    else:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            return False


# Check dependencies
required_deps = [
    "django",
    "rest_framework",
    "cv2",
    "numpy",
    "onnxruntime",
    "bleak",
    "waitress",
]
if config.get("database_engine") == "postgresql":
    required_deps.append("psycopg")

missing = [name for name in required_deps if importlib.util.find_spec(name) is None]
if missing:
    raise SystemExit(
        "Missing dependencies: "
        + ", ".join(missing)
        + ". Run 'pip install -r requirements-revision.txt'"
    )

# Start PostgreSQL if configured
if config.get("database_engine") == "postgresql":
    pg_bin = Path("/opt/homebrew/opt/postgresql@17/bin")
    if not (pg_bin / "pg_ctl").exists():
        found = shutil.which("pg_ctl")
        pg_bin = Path(found).parent if found else None

    if pg_bin:
        pg_ctl = str(pg_bin / "pg_ctl")
        pg_data = str(RUNTIME / "postgres")
        if subprocess.run(
            [pg_ctl, "-D", pg_data, "status"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode:
            subprocess.run(
                [
                    pg_ctl,
                    "-D",
                    pg_data,
                    "-l",
                    str(RUNTIME / "postgres.log"),
                    "start",
                ],
                check=True,
            )

# Check for existing running instances
for name in ["backend", "recognition", "https"]:
    pidfile = RUNTIME / f"{name}.pid"
    if pidfile.exists():
        try:
            pid = int(pidfile.read_text().strip())
            if is_pid_alive(pid):
                raise SystemExit(f"{name} is already running (PID {pid}). Run 'python scripts/stop.py' first.")
        except ValueError:
            pass
        pidfile.unlink(missing_ok=True)

python_exe = get_python_exe()
use_https = config.get("https", False)
is_foreground = "-f" in sys.argv or "--foreground" in sys.argv

env = {
    **os.environ,
    "PYTHONUNBUFFERED": "1",
    "PYTHONPATH": f"{str(BACKEND)}{os.pathsep}{str(ROOT)}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
}

waitress_cmd = [
    python_exe,
    "-m",
    "waitress",
    "--listen=127.0.0.1:8000",
    "--threads=16",
]
if use_https:
    waitress_cmd.extend([
        "--trusted-proxy=127.0.0.1",
        "--trusted-proxy-headers=x-forwarded-for x-forwarded-proto",
    ])
waitress_cmd.append("config.wsgi:application")

commands = {
    "backend": (waitress_cmd, BACKEND),
    "recognition": ([python_exe, str(ROOT / "revision" / "worker.py")], ROOT),
}

if use_https:
    caddy_bin = find_tool("caddy", "/opt/homebrew/bin/caddy")
    if caddy_bin:
        commands["https"] = (
            [
                str(caddy_bin),
                "run",
                "--config",
                str(RUNTIME / "Caddyfile"),
                "--adapter",
                "caddyfile",
            ],
            ROOT,
        )

started = []
try:
    for name, (command, cwd) in commands.items():
        if is_foreground:
            popen_kwargs = {
                "cwd": str(cwd),
                "env": env,
            }
        else:
            log = (RUNTIME / f"{name}.log").open("ab")
            popen_kwargs = {
                "cwd": str(cwd),
                "env": env,
                "stdout": log,
                "stderr": subprocess.STDOUT,
            }
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = (
                    subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                )
            else:
                popen_kwargs["start_new_session"] = True

        proc = subprocess.Popen(command, **popen_kwargs)
        if not is_foreground:
            log.close()
        (RUNTIME / f"{name}.pid").write_text(str(proc.pid))
        started.append((name, proc))
        time.sleep(0.8)
        if proc.poll() is not None:
            raise RuntimeError(f"{name} failed to start. See runtime/{name}.log for details.")
except BaseException:
    for name, p in started:
        if p.poll() is None:
            p.terminate()
        (RUNTIME / f"{name}.pid").unlink(missing_ok=True)
    raise

print("=" * 60)
print("PNP Access Control Services Started")
print("=" * 60)

hosts = config.get("hosts", ["localhost"])
if use_https:
    print("\nLocal monitor: https://localhost:8443/monitor")
    print("Admin dashboard addresses:")
    for host in hosts:
        print(f"  https://{host}:8443/")
else:
    print("\nLocal monitor: http://localhost:8000/monitor")
    print("Admin dashboard addresses:")
    for host in hosts:
        print(f"  http://{host}:8000/")

print("\nCamera remains off until started in the monitor.")

if is_foreground:
    print("\n[Running in foreground. Press Ctrl+C to stop services.]")
    try:
        while True:
            for name, proc in started:
                if proc.poll() is not None:
                    print(f"\n{name} process terminated unexpectedly.")
                    sys.exit(1)
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping services...")
    finally:
        for name, proc in started:
            if proc.poll() is None:
                proc.terminate()
            (RUNTIME / f"{name}.pid").unlink(missing_ok=True)
        print("Services stopped.")
else:
    print("To stop all services, run: python scripts/stop.py")
