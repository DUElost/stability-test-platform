import sys
import threading
import time
import unittest
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch, call

project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

try:
    from backend.agent.main import complete_run, HeartbeatThread
except ModuleNotFoundError:
    from agent.main import complete_run, HeartbeatThread


class TestAgentMain(unittest.TestCase):
    def test_complete_run_includes_artifact_payload(self):
        artifact = {
            "storage_uri": "file:///tmp/88.tar.gz",
            "size_bytes": 2048,
            "checksum": "b" * 64,
        }
        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.raise_for_status.return_value = None
            mock_post.return_value = mock_resp

            complete_run(
                "http://127.0.0.1:8000",
                88,
                {
                    "status": "FINISHED",
                    "exit_code": 0,
                    "error_code": None,
                    "error_message": None,
                    "log_summary": "ok",
                    "artifact": artifact,
                },
                fencing_token="0:1",
            )

            called_payload = mock_post.call_args.kwargs["json"]
            self.assertEqual(called_payload["update"]["status"], "FINISHED")
            self.assertEqual(called_payload["artifact"], artifact)


class TestHeartbeatThread(unittest.TestCase):
    """Test that HeartbeatThread continues sending heartbeats during long-running tasks."""

    @patch("backend.agent.heartbeat_thread.send_heartbeat")
    @patch("backend.agent.heartbeat_thread.device_discovery")
    def test_heartbeat_continues_during_long_task(self, mock_discovery, mock_send_hb):
        """Verify heartbeat fires multiple times while a simulated task blocks the main thread."""
        # Mock device discovery to return empty list quickly
        mock_discovery.discover_devices.return_value = []

        # Track heartbeat call timestamps
        hb_timestamps = []
        original_send = mock_send_hb.side_effect

        def track_heartbeat(*args, **kwargs):
            hb_timestamps.append(time.monotonic())

        mock_send_hb.side_effect = track_heartbeat

        poll_interval = 0.2  # Fast interval for testing
        ht = HeartbeatThread(
            api_url="http://127.0.0.1:8000",
            host_id=1,
            adb_path="adb",
            mount_points=[],
            host_info={"ip": "127.0.0.1"},
            poll_interval=poll_interval,
            sio_client=None,  # No WS, use HTTP fallback
        )

        ht.start()

        # Simulate a long-running task on the main thread (blocks for 1 second)
        time.sleep(1.0)

        ht.stop()

        # Heartbeat should have fired at least 3 times during the 1-second simulated task
        # (once immediately on start, plus ~4 more at 0.2s intervals)
        self.assertGreaterEqual(
            len(hb_timestamps), 3,
            f"Expected at least 3 heartbeats during 1s task, got {len(hb_timestamps)}",
        )

        # Verify heartbeats were sent at roughly poll_interval spacing
        if len(hb_timestamps) >= 2:
            gaps = [
                hb_timestamps[i + 1] - hb_timestamps[i]
                for i in range(len(hb_timestamps) - 1)
            ]
            avg_gap = sum(gaps) / len(gaps)
            self.assertLess(
                avg_gap, poll_interval * 3,
                f"Average heartbeat gap {avg_gap:.2f}s too large (expected ~{poll_interval}s)",
            )

    @patch("backend.agent.heartbeat_thread.send_heartbeat")
    @patch("backend.agent.heartbeat_thread.device_discovery")
    def test_heartbeat_ws_fallback_to_http(self, mock_discovery, mock_send_hb):
        """When WS client is disconnected, heartbeat falls back to HTTP."""
        mock_discovery.discover_devices.return_value = []

        # Create a mock WS client that reports disconnected
        mock_ws = MagicMock()
        mock_ws.connected = False

        ht = HeartbeatThread(
            api_url="http://127.0.0.1:8000",
            host_id=1,
            adb_path="adb",
            mount_points=[],
            host_info={"ip": "127.0.0.1"},
            poll_interval=0.1,
            sio_client=mock_ws,
        )

        ht.start()
        time.sleep(0.35)
        ht.stop()

        # HTTP send_heartbeat should have been called (WS was disconnected)
        self.assertGreaterEqual(mock_send_hb.call_count, 2)
        # WS send_heartbeat should NOT have been called
        mock_ws.send_heartbeat.assert_not_called()

    @patch("backend.agent.heartbeat_thread.send_heartbeat")
    @patch("backend.agent.heartbeat_thread.device_discovery")
    def test_heartbeat_stop_terminates_thread(self, mock_discovery, mock_send_hb):
        """HeartbeatThread.stop() should terminate the thread promptly."""
        mock_discovery.discover_devices.return_value = []

        ht = HeartbeatThread(
            api_url="http://127.0.0.1:8000",
            host_id=1,
            adb_path="adb",
            mount_points=[],
            host_info={"ip": "127.0.0.1"},
            poll_interval=10.0,  # Long interval
            sio_client=None,
        )

        ht.start()
        time.sleep(0.1)  # Let it start
        ht.stop()

        # Thread should be dead after stop()
        self.assertFalse(ht._thread.is_alive())

    @patch("backend.agent.heartbeat_thread.send_heartbeat")
    @patch("backend.agent.heartbeat_thread.device_discovery")
    def test_device_reconnect_triggers_recovery_callback(self, mock_discovery, mock_send_hb):
        """ADB state false -> true should invoke the reconnect callback once."""
        mock_discovery.discover_devices.return_value = [{"serial": "ABC123", "adb_state": "device"}]
        mock_discovery.collect_device_info.side_effect = [
            {"adb_state": "offline", "adb_connected": False},
            {"adb_state": "device", "adb_connected": True},
        ]
        reconnect_cb = MagicMock()

        ht = HeartbeatThread(
            api_url="http://127.0.0.1:8000",
            host_id=1,
            adb_path="adb",
            mount_points=[],
            host_info={"ip": "127.0.0.1"},
            poll_interval=0.1,
            sio_client=None,
            on_devices_reconnected=reconnect_cb,
        )

        ht._tick()
        reconnect_cb.assert_not_called()

        ht._tick()
        reconnect_cb.assert_called_once_with(["ABC123"])

    @patch("backend.agent.heartbeat_thread.send_heartbeat")
    @patch("backend.agent.heartbeat_thread.device_discovery")
    def test_device_missing_then_reappearing_triggers_recovery_callback(self, mock_discovery, mock_send_hb):
        """设备先从 adb discovery 消失，再重新出现时，也应触发重连回调。"""
        mock_discovery.discover_devices.side_effect = [
            [{"serial": "ABC123", "adb_state": "device"}],
            [],
            [{"serial": "ABC123", "adb_state": "device"}],
        ]
        mock_discovery.collect_device_info.side_effect = [
            {"adb_state": "device", "adb_connected": True},
            {"adb_state": "device", "adb_connected": True},
        ]
        reconnect_cb = MagicMock()

        ht = HeartbeatThread(
            api_url="http://127.0.0.1:8000",
            host_id=1,
            adb_path="adb",
            mount_points=[],
            host_info={"ip": "127.0.0.1"},
            poll_interval=0.1,
            sio_client=None,
            on_devices_reconnected=reconnect_cb,
        )

        ht._tick()
        reconnect_cb.assert_not_called()

        ht._tick()
        reconnect_cb.assert_not_called()

        ht._tick()
        reconnect_cb.assert_called_once_with(["ABC123"])

    @patch("backend.agent.heartbeat_thread.send_heartbeat")
    @patch("backend.agent.heartbeat_thread.device_discovery")
    def test_device_reconnect_after_heartbeat_failure_retries_callback(self, mock_discovery, mock_send_hb):
        """接回设备那一拍若心跳失败，下一拍仍应补触发恢复回调。"""
        mock_discovery.discover_devices.side_effect = [
            [{"serial": "ABC123", "adb_state": "device"}],
            [],
            [{"serial": "ABC123", "adb_state": "device"}],
            [{"serial": "ABC123", "adb_state": "device"}],
        ]
        mock_discovery.collect_device_info.side_effect = [
            {"adb_state": "device", "adb_connected": True},
            {"adb_state": "device", "adb_connected": True},
            {"adb_state": "device", "adb_connected": True},
        ]
        mock_send_hb.side_effect = [
            {"ok": True},
            {"ok": True},
            None,
            {"ok": True},
        ]
        reconnect_cb = MagicMock()

        ht = HeartbeatThread(
            api_url="http://127.0.0.1:8000",
            host_id=1,
            adb_path="adb",
            mount_points=[],
            host_info={"ip": "127.0.0.1"},
            poll_interval=0.1,
            sio_client=None,
            on_devices_reconnected=reconnect_cb,
        )

        ht._tick()
        reconnect_cb.assert_not_called()

        ht._tick()
        reconnect_cb.assert_not_called()

        ht._tick()
        reconnect_cb.assert_not_called()

        ht._tick()
        reconnect_cb.assert_called_once_with(["ABC123"])


class TestStartupAeeStateMigration(unittest.TestCase):
    @staticmethod
    def _init_agent_state(db_path: Path):
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS agent_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        return conn

    def test_startup_migrates_legacy_aee_state_keys_before_jobs_run(self):
        from backend.agent.main import _migrate_legacy_aee_state_on_startup

        db_path = Path(self._testMethodName).with_suffix(".db")
        if db_path.exists():
            db_path.unlink()
        conn = self._init_agent_state(db_path)
        try:
            conn.execute(
                "INSERT INTO agent_state(key, value) VALUES (?, ?)",
                ("scan_aee:SX:aee_exp:processed_entries", '["legacy-line"]'),
            )
            conn.execute(
                "INSERT INTO agent_state(key, value) VALUES (?, ?)",
                ("scan_aee:SX:aee_exp:pending_pull", '{"legacy-line":{"db_path":"/data/aee_exp/db.1"}}'),
            )
            conn.commit()
        finally:
            conn.close()

        summary = _migrate_legacy_aee_state_on_startup(str(db_path))

        self.assertEqual(summary["processed_entries_migrated"], 1)
        self.assertEqual(summary["pending_pull_migrated"], 1)
        conn = sqlite3.connect(db_path)
        try:
            watcher_processed = conn.execute(
                "SELECT value FROM agent_state WHERE key=?",
                ("watcher:aee:SX:aee_exp:processed_entries",),
            ).fetchone()
            watcher_pending = conn.execute(
                "SELECT value FROM agent_state WHERE key=?",
                ("watcher:aee:SX:aee_exp:pending_pull",),
            ).fetchone()
            self.assertIsNotNone(watcher_processed)
            self.assertIsNotNone(watcher_pending)
        finally:
            conn.close()
            if db_path.exists():
                db_path.unlink()


if __name__ == "__main__":
    unittest.main()
