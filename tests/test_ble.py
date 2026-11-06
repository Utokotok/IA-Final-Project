import asyncio
import io
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from services.recognition.ble_scanner import BLE_RESULT_TTL, AdvertisementCache, BLEScanner, verify_tag


def advertisement(tag=b"tag-one", rssi=-60):
    return SimpleNamespace(
        manufacturer_data={65535: tag}, service_uuids=[], local_name="TAG", rssi=rssi
    )


class FreshnessTests(unittest.TestCase):
    def test_each_device_expires_independently_and_snapshots_do_not_renew_it(self):
        cache = AdvertisementCache()
        cache.update(SimpleNamespace(address="a"), advertisement(), now=10)
        cache.update(SimpleNamespace(address="b"), advertisement(b"tag-two"), now=11)
        self.assertEqual(cache.snapshot(now=12)["A"]["timestamp"], 10)
        self.assertEqual(cache.snapshot(now=13.1).keys(), {"B"})
        self.assertEqual(cache.snapshot(now=14.1), {})

    def test_received_snapshots_keep_original_age_and_verification_expires(self):
        cache = AdvertisementCache()
        cache.update(SimpleNamespace(address="a"), advertisement(), now=10)
        devices = cache.snapshot(now=10)
        scanner = BLEScanner()
        scanner._apply_update({"devices": devices, "scanning": True}, now=11)
        self.assertEqual(scanner.latest_results["A"].timestamp, 10)
        with patch("services.recognition.ble_scanner.time.monotonic", return_value=11):
            self.assertTrue(verify_tag("tag-one", scanner.latest_results))
        scanner._apply_update({"devices": devices, "scanning": True}, now=14)
        self.assertEqual(scanner.latest_results, {})
        self.assertTrue(scanner.scanning)

    def test_unrelated_or_weaker_advertisements_do_not_authorize_a_tag(self):
        cache = AdvertisementCache()
        cache.update(SimpleNamespace(address="a"), advertisement(rssi=-90), now=10)
        cache.update(
            SimpleNamespace(address="unrelated"),
            SimpleNamespace(
                manufacturer_data={}, service_uuids=[], local_name="Phone", rssi=-30
            ),
            now=11,
        )
        scanner = BLEScanner()
        scanner._apply_update(
            {"devices": cache.snapshot(now=11), "scanning": True}, now=11
        )
        self.assertEqual(scanner.latest_results.keys(), {"A"})
        with patch("services.recognition.ble_scanner.time.monotonic", return_value=11):
            self.assertFalse(verify_tag("tag-one", scanner.latest_results))

    def test_registration_can_request_refresh_while_continuous_scan_is_active(self):
        scanner = BLEScanner()
        scanner.enabled = scanner.scanning = True
        scanner.start = Mock()
        scanner._proc = Mock()
        scanner._proc.poll.return_value = None
        scanner.request_scan()
        scanner._proc.stdin.write.assert_called_once_with("scan\n")
        self.assertTrue(scanner.state()["continuous"])
        self.assertLessEqual(scanner.state()["update_interval"], 0.5)
        self.assertEqual(scanner.state()["result_ttl"], 3)

    def test_stopping_scanner_removes_cached_identities(self):
        cache = AdvertisementCache()
        cache.update(SimpleNamespace(address="a"), advertisement(), now=10)
        scanner = BLEScanner()
        scanner._apply_update(
            {"devices": cache.snapshot(now=10), "scanning": True}, now=10
        )
        scanner.stop()
        self.assertFalse(scanner.scanning)
        self.assertEqual(scanner.latest_results, {})


class ContinuousWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_scanner_publishes_repeatedly_until_cancelled(self):
        from services.recognition.ble_scanner import _worker_async_loop

        messages = []
        received = asyncio.Event()
        stopped = asyncio.Event()
        entered = 0

        class FakeScanner:
            def __init__(self, detection_callback):
                self.callback = detection_callback

            async def __aenter__(self):
                nonlocal entered
                entered += 1
                self.callback(SimpleNamespace(address="beacon"), advertisement())
                return self

            async def __aexit__(self, *args):
                stopped.set()

        def capture(value, **kwargs):
            payload = json.loads(value)
            messages.append(payload)
            if len(messages) >= 3:
                received.set()

        with (
            patch("bleak.BleakScanner", FakeScanner),
            patch("services.recognition.ble_scanner.BLE_UPDATE_INTERVAL", 0.02),
            patch("services.recognition.ble_scanner.sys.stdin", io.StringIO("")),
            patch("builtins.print", capture),
        ):
            task = asyncio.create_task(_worker_async_loop())
            try:
                await asyncio.wait_for(received.wait(), timeout=1)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertEqual(entered, 1)
        self.assertTrue(stopped.is_set())
        self.assertTrue(all(message["scanning"] for message in messages))
        stamps = [message["devices"]["BEACON"]["timestamp"] for message in messages]
        self.assertEqual(len(set(stamps)), 1)
        self.assertLess(time.monotonic() - stamps[0], BLE_RESULT_TTL)
