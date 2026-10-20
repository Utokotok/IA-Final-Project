#!/usr/bin/env python3
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
runtime = ROOT / "runtime"
stopping = False


def stop(*_):
    global stopping
    stopping = True


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
try:
    while not stopping:
        healthy = True
        for name in ["https", "recognition", "backend"]:
            try:
                os.kill(int((runtime / (name + ".pid")).read_text()), 0)
            except (OSError, ValueError):
                healthy = False
        if not healthy:
            subprocess.run([sys.executable, str(ROOT / "scripts/stop.py")])
            subprocess.run([sys.executable, str(ROOT / "scripts/start.py")])
        time.sleep(5)
finally:
    subprocess.run([sys.executable, str(ROOT / "scripts/stop.py")])
