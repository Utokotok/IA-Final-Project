#!/usr/bin/env python3
import os
import signal
import subprocess
from pathlib import Path

runtime = Path(__file__).resolve().parents[1] / "runtime"
for name in ["https", "recognition", "backend"]:
    file = runtime / (name + ".pid")
    if not file.exists():
        continue
    try:
        pid = int(file.read_text())
        command = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        expected = (
            str(runtime / "Caddyfile")
            if name == "https"
            else str(
                runtime.parent
                / (
                    ".venv/bin/waitress-serve"
                    if name == "backend"
                    else "revision/worker.py"
                )
            )
        )
        if expected in command:
            os.killpg(pid, signal.SIGTERM)
    except (OSError, ValueError):
        pass
    file.unlink(missing_ok=True)
print("Application services stopped. PostgreSQL remains available for maintenance.")
