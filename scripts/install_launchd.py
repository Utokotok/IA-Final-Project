#!/usr/bin/env python3
"""Generate, but do not automatically install, a per-user launchd supervisor."""

import plistlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
runtime = ROOT / "runtime"
agent = {
    "Label": "local.pnp.supervisor",
    "ProgramArguments": [
        str(ROOT / ".venv/bin/python"),
        str(ROOT / "scripts/supervise.py"),
    ],
    "WorkingDirectory": str(ROOT),
    "RunAtLoad": True,
    "KeepAlive": True,
    "StandardOutPath": str(runtime / "supervisor.log"),
    "StandardErrorPath": str(runtime / "supervisor.log"),
    "ThrottleInterval": 10,
}
(runtime / "local.pnp.supervisor.plist").write_bytes(plistlib.dumps(agent))
print(
    "Generated runtime/local.pnp.supervisor.plist. See SETUP.md for install/removal instructions."
)
