import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

project_root = Path(__file__).parent.parent.parent
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
            )

            called_payload = mock_post.call_args.kwargs["json"]
            self.assertEqual(called_payload["update"]["status"], "FINISHED")
            self.assertEqual(called_payload["artifact"], artifact)


class TestHeartbeatThread(unittest.TestCase):
    """Test that HeartbeatThread continues sending heartbeats during long-running tasks."""

    @patch("backend.agent.main.send_heartbeat")
    @patch("backend.agent.main.device_discovery")
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
            ws_client=None,  # No WS, use HTTP fallback
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

    @patch("backend.agent.main.send_heartbeat")
    @patch("backend.agent.main.device_discovery")
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
            ws_client=mock_ws,
        )

        ht.start()
        time.sleep(0.35)
        ht.stop()

        # HTTP send_heartbeat should have been called (WS was disconnected)
        self.assertGreaterEqual(mock_send_hb.call_count, 2)
        # WS send_heartbeat should NOT have been called
        mock_ws.send_heartbeat.assert_not_called()

    @patch("backend.agent.main.send_heartbeat")
    @patch("backend.agent.main.device_discovery")
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
            ws_client=None,
        )

        ht.start()
        time.sleep(0.1)  # Let it start
        ht.stop()

        # Thread should be dead after stop()
        self.assertFalse(ht._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
