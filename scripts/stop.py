#!/usr/bin/env python3
"""Cross-platform service shutdown for PNP Access Control."""

import os
import signal
import subprocess
import sys
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"


def stop_process(pid: int):
    if sys.platform == "win32":
        try:
            # /F force, /T tree kill
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
    else:
        try:
            os.killpg(pid, signal.SIGTERM)
        except (OSError, AttributeError):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass


for name in ["https", "recognition", "backend"]:
    pidfile = RUNTIME / f"{name}.pid"
    if not pidfile.exists():
        continue
    try:
        pid = int(pidfile.read_text().strip())
        stop_process(pid)
    except (OSError, ValueError):
        pass
    pidfile.unlink(missing_ok=True)

print("Application services stopped.")
