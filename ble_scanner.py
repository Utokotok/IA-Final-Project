"""BLE second-factor scanner for the face-recognition access-control system.
Updated for dynamic, arbitrary BLE tag enrollment.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# The interval only backs off after a scanner failure; healthy scanning never pauses.
BLE_RETRY_INTERVAL = float(
    os.environ.get("BLE_RETRY_INTERVAL", os.environ.get("BLE_SCAN_INTERVAL", "5.0"))
)
BLE_UPDATE_INTERVAL = max(0.05, float(os.environ.get("BLE_UPDATE_INTERVAL", "0.25")))
BLE_RSSI_FLOOR = int(os.environ.get("BLE_RSSI_FLOOR", "-80"))
BLE_RESULT_TTL = float(os.environ.get("BLE_RESULT_TTL", "3.0"))
BLE_ENABLED = os.environ.get("BLE_DISABLED", "").strip() not in {"1", "true", "yes"}

MFG_COMPANY_ID = 0xFFFF


@dataclass
class ScanEntry:
    address: str
    rssi: int
    uuids: list[str]
    mfr_data: dict[int, bytes]
    timestamp: float = field(default_factory=time.monotonic)


def verify_tag(user_ble_id: str | None, scan_results: dict[str, ScanEntry]) -> bool:
    """Return True if the specific dynamic BLE identifier stored for this user is visible."""
    if not user_ble_id:
        return True  # Face-only user

    target_id = user_ble_id.strip().lower()
    now = time.monotonic()

    for entry in scan_results.values():
        if now - entry.timestamp > BLE_RESULT_TTL:
            continue
        if entry.rssi < BLE_RSSI_FLOOR:
            continue

        # Check manufacturer data payload (e.g., string identifier flashed on ESP32)
        payload = entry.mfr_data.get(MFG_COMPANY_ID)
        if payload:
            mfr_text = payload.decode("utf-8", errors="ignore").strip().lower()
            if mfr_text == target_id:
                return True

        # Check if the target ID matches any service UUID string fragment
        for u in entry.uuids:
            if target_id in u:
                return True

    return False


class AdvertisementCache:
    """Only an actual qualifying advertisement renews a device's last-seen time."""

    def __init__(self):
        self.devices = {}

    def update(self, device, adv, now=None):
        mfr = adv.manufacturer_data or {}
        if not (
            MFG_COMPANY_ID in mfr
            or adv.service_uuids
            or (adv.local_name and "TAG" in adv.local_name)
        ):
            return
        self.devices[device.address.upper()] = {
            "rssi": adv.rssi if adv.rssi is not None else -999,
            "uuids": [str(u).lower() for u in (adv.service_uuids or [])],
            "mfr_data": {str(int(cid)): bytes(data).hex() for cid, data in mfr.items()},
            "timestamp": time.monotonic() if now is None else now,
        }

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        self.devices = {
            address: item
            for address, item in self.devices.items()
            if now - item["timestamp"] <= BLE_RESULT_TTL
        }
        return dict(self.devices)


class BLEScanner:
    def __init__(self) -> None:
        self.enabled = BLE_ENABLED
        self.latest_results: dict[str, ScanEntry] = {}
        self.last_scan_time: float | None = None
        self.scanning = False
        self.last_error = ""
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen[str] | None = None

    def start(self) -> None:
        if not self.enabled:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="ble-scanner", daemon=True
        )
        self._thread.start()

    def state(self) -> dict:
        with self._lock:
            return {
                "enabled": self.enabled,
                "scanning": self.scanning,
                "last_scan": self.last_scan_time,
                "error": self.last_error,
                "continuous": True,
                "update_interval": BLE_UPDATE_INTERVAL,
                "result_ttl": BLE_RESULT_TTL,
            }

    def request_scan(self) -> None:
        if not self.enabled:
            self.last_error = "Bluetooth scanning is disabled (BLE_DISABLED)."
            return
        self.start()
        with self._lock:
            proc = self._proc
            if proc and proc.poll() is None and proc.stdin:
                try:
                    proc.stdin.write("scan\n")
                    proc.stdin.flush()
                    self.scanning = True
                except (BrokenPipeError, OSError):
                    self.last_error = "Bluetooth scanner restarted. Please try again."

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            self.scanning = False
            self.latest_results = {}
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except OSError:
                pass

    def _apply_update(self, update, now=None):
        now = time.monotonic() if now is None else now
        fresh = None
        if "devices" in update:
            fresh = {}
            for address, item in update["devices"].items():
                # Both processes share the same host monotonic clock. Repeated
                # snapshots must preserve advertisement age, not renew it.
                stamp = float(item["timestamp"])
                if (
                    not math.isfinite(stamp)
                    or stamp > now
                    or now - stamp > BLE_RESULT_TTL
                ):
                    continue
                fresh[address] = ScanEntry(
                    address=address,
                    rssi=int(item["rssi"]),
                    uuids=list(item["uuids"]),
                    mfr_data={
                        int(cid): bytes.fromhex(value)
                        for cid, value in item.get("mfr_data", {}).items()
                    },
                    timestamp=stamp,
                )
        with self._lock:
            self.scanning = bool(update.get("scanning", False))
            self.last_error = update.get("error", "")
            if fresh is not None:
                self.latest_results = fresh
                self.last_scan_time = now

    def _run(self) -> None:
        script_path = os.path.abspath(__file__)
        while not self._stop_event.is_set():
            try:
                self._proc = subprocess.Popen(
                    [sys.executable, "-u", script_path, "--worker"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                )
                assert self._proc.stdout is not None
                for line in self._proc.stdout:
                    if self._stop_event.is_set():
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw_items = json.loads(line)
                    except ValueError:
                        continue

                    self._apply_update(raw_items)
            except Exception as exc:
                self.last_error = str(exc)
                log.exception("BLE scanner supervisor error.")
            finally:
                with self._lock:
                    self.scanning = False
                    self.latest_results = {}
                if not self._stop_event.is_set() and not self.last_error:
                    self.last_error = "Bluetooth scanner exited. Check Bluetooth permissions for your terminal."
                if self._proc and self._proc.poll() is None:
                    try:
                        self._proc.terminate()
                    except OSError:
                        pass

            deadline = time.monotonic() + BLE_RETRY_INTERVAL
            while not self._stop_event.is_set() and time.monotonic() < deadline:
                time.sleep(0.25)


async def _worker_async_loop() -> None:
    from bleak import BleakScanner

    wake = asyncio.Event()
    loop = asyncio.get_running_loop()
    cache = AdvertisementCache()

    def commands():
        for line in sys.stdin:
            if line.strip() == "scan":
                loop.call_soon_threadsafe(wake.set)

    threading.Thread(target=commands, daemon=True).start()
    # Keep the adapter scanning. Callbacks collect advertisements immediately;
    # publish bounded snapshots without repeatedly stopping/starting Bluetooth.
    async with BleakScanner(
        detection_callback=lambda device, adv: cache.update(device, adv)
    ):
        while True:
            print(
                json.dumps(
                    {"devices": cache.snapshot(), "scanning": True, "error": ""}
                ),
                flush=True,
            )
            try:
                await asyncio.wait_for(wake.wait(), timeout=BLE_UPDATE_INTERVAL)
            except asyncio.TimeoutError:
                pass
            wake.clear()


def _run_worker() -> None:
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.ole32.CoInitializeEx(None, 0)
        except Exception:
            pass
    try:
        asyncio.run(_worker_async_loop())
    except Exception as exc:
        print(
            json.dumps(
                {
                    "error": f"Bluetooth scanner could not start: {exc}",
                    "scanning": False,
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    if "--worker" in sys.argv:
        _run_worker()
