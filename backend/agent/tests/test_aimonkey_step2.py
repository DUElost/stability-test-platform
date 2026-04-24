import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from backend.agent.aimonkey_aee import (
    AEEEntry,
    pull_aee_entries,
    scan_aee_entries,
    scan_and_pull_aee_entries,
)
from backend.agent.aimonkey_risk import build_risk_summary, write_risk_summary


class FakeAdb:
    def __init__(self):
        self.pulled = []

    def shell(self, serial, cmd):
        joined = " ".join(cmd)
        result = MagicMock()
        if "/data/aee_exp" in joined:
            result.stdout = "AEE_ANR_0001\nAEE_CRASH_0002\n"
        elif "/data/vendor/aee_exp" in joined:
            result.stdout = "vendor_event_001\n"
        else:
            result.stdout = ""
        return result

    def pull(self, serial, remote_path, local_path):
        self.pulled.append((serial, remote_path, local_path))
        return MagicMock(returncode=0)


class TestAIMonkeyStep2(unittest.TestCase):
    def test_scan_aee_entries(self):
        adb = FakeAdb()
        entries = scan_aee_entries(adb, "SERIAL_X")
        self.assertEqual(len(entries), 3)
        event_types = sorted([entry.event_type for entry in entries])
        self.assertIn("ANR", event_types)
        self.assertIn("CRASH", event_types)

    def test_pull_and_scan_and_pull(self):
        adb = FakeAdb()
        with tempfile.TemporaryDirectory() as temp_dir:
            entries = [
                AEEEntry("aee_exp", "AEE_ANR_0001", "/data/aee_exp/AEE_ANR_0001", "ANR"),
                AEEEntry("aee_exp_vendor", "vendor_event_001", "/data/vendor/aee_exp/vendor_event_001", "AEE"),
            ]
            pulled = pull_aee_entries(adb, "SERIAL_X", temp_dir, entries)
            self.assertEqual(len(pulled), 2)
            self.assertEqual(sum(1 for item in pulled if item["pulled"]), 2)

            scanned, pulled_again = scan_and_pull_aee_entries(adb, "SERIAL_X", temp_dir)
            self.assertEqual(len(scanned), 3)
            self.assertEqual(len(pulled_again), 3)

    def test_build_and_write_risk_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            logcat_path = Path(temp_dir) / "logcat.txt"
            logcat_path.write_text(
                "I ActivityManager: ANR in com.example.app\n"
                "E AndroidRuntime: FATAL EXCEPTION: main\n",
                encoding="utf-8",
            )
            entries = [
                AEEEntry("aee_exp", "AEE_ANR_0001", "/data/aee_exp/AEE_ANR_0001", "ANR"),
            ]
            summary = build_risk_summary(
                monitor_summary="restart 1; monkey died after 1 restarts",
                logcat_path=logcat_path,
                aee_entries=entries,
            )
            self.assertEqual(summary["risk_level"], "HIGH")
            self.assertGreaterEqual(summary["counts"]["events_total"], 3)

            output = Path(temp_dir) / "risk_summary.json"
            write_risk_summary(summary, output)
            self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()
