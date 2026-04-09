"""WebSocket / SocketIO contract tests (ADR-0009, Phase 3 migration).

Validates that:
- Legacy WS stub endpoints accept connections (deprecated but functional)
- All broadcast messages follow the standard envelope: {type, payload, timestamp}
- Broadcast helpers emit via SocketIO with correct event/namespace/room
"""

import json
import os
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from starlette.testclient import TestClient

from backend.main import fastapi_app


@pytest.fixture
def ws_client():
    with TestClient(fastapi_app) as c:
        yield c


# ---------------------------------------------------------------------------
# Connection — legacy WS stub endpoints (deprecated, accept-and-hold)
# ---------------------------------------------------------------------------

class TestDashboardWS:
    def test_connect_with_valid_token(self, ws_client):
        with ws_client.websocket_connect("/ws/dashboard?token=dev-token-12345") as ws:
            data = ws.receive_json()
            assert data["type"] == "DEPRECATED"

    def test_connect_without_token_dev_mode(self, ws_client):
        with ws_client.websocket_connect("/ws/dashboard") as ws:
            data = ws.receive_json()
            assert data["type"] == "DEPRECATED"


class TestLogsWS:
    def test_connect_logs_endpoint(self, ws_client):
        with ws_client.websocket_connect("/ws/logs/999?token=dev-token-12345") as ws:
            ws.send_text("{}")

    def test_connect_job_logs_endpoint(self, ws_client):
        with ws_client.websocket_connect("/ws/jobs/999/logs?token=dev-token-12345") as ws:
            ws.send_text("{}")


class TestWorkflowRunWS:
    def test_connect_workflow_run_endpoint(self, ws_client):
        with ws_client.websocket_connect("/ws/workflow-runs/999?token=dev-token-12345") as ws:
            ws.send_text("{}")


class TestAgentWS:
    def test_agent_ws_stub_accepts(self, ws_client):
        """Deprecated agent WS endpoint accepts connection and sends DEPRECATED notice."""
        with ws_client.websocket_connect("/ws/agent/test-host-001") as ws:
            data = ws.receive_json()
            assert data["type"] == "DEPRECATED"


# ---------------------------------------------------------------------------
# Envelope contract: every broadcast message must have {type, payload, timestamp}
# Uses mock SocketIO server to capture emitted events.
# ---------------------------------------------------------------------------

def _make_mock_sio():
    """Create a mock AsyncServer that captures emit() calls."""
    mock_sio = AsyncMock()
    mock_sio.emit = AsyncMock()
    return mock_sio


class TestBroadcastEnvelope:
    """Test that broadcast helpers produce messages with the standard envelope."""

    @pytest.mark.asyncio
    async def test_device_update_envelope(self):
        from backend.realtime.socketio_server import broadcast_device_update

        mock_sio = _make_mock_sio()
        with patch("backend.realtime.socketio_server._sio", mock_sio):
            await broadcast_device_update({"id": 1, "status": "ONLINE"})

        mock_sio.emit.assert_called_once()
        args, kwargs = mock_sio.emit.call_args
        assert args[0] == "device_update"
        msg = args[1]
        assert msg["type"] == "DEVICE_UPDATE"
        assert "payload" in msg
        assert "timestamp" in msg
        assert msg["payload"]["id"] == 1
        assert kwargs["namespace"] == "/dashboard"

    @pytest.mark.asyncio
    async def test_job_status_envelope(self):
        from backend.realtime.socketio_server import broadcast_run_job_update

        mock_sio = _make_mock_sio()
        with patch("backend.realtime.socketio_server._sio", mock_sio):
            await broadcast_run_job_update(run_id=10, job_id=42, status="COMPLETED")

        mock_sio.emit.assert_called_once()
        args, kwargs = mock_sio.emit.call_args
        assert args[0] == "job_status"
        msg = args[1]
        assert msg["type"] == "JOB_STATUS"
        assert msg["payload"]["job_id"] == 42
        assert msg["payload"]["status"] == "COMPLETED"
        assert "timestamp" in msg
        assert kwargs["room"] == "workflow:10"

    @pytest.mark.asyncio
    async def test_workflow_status_envelope(self):
        from backend.realtime.socketio_server import broadcast_run_workflow_status

        mock_sio = _make_mock_sio()
        with patch("backend.realtime.socketio_server._sio", mock_sio):
            await broadcast_run_workflow_status(run_id=10, status="SUCCESS")

        mock_sio.emit.assert_called_once()
        args, kwargs = mock_sio.emit.call_args
        msg = args[1]
        assert msg["type"] == "WORKFLOW_STATUS"
        assert msg["payload"]["status"] == "SUCCESS"
        assert "timestamp" in msg
        assert kwargs["room"] == "workflow:10"

    @pytest.mark.asyncio
    async def test_run_update_envelope(self):
        from backend.realtime.socketio_server import broadcast_run_update

        mock_sio = _make_mock_sio()
        with patch("backend.realtime.socketio_server._sio", mock_sio):
            await broadcast_run_update(run_id=5, task_id=3, status="RUNNING", progress=50)

        mock_sio.emit.assert_called_once()
        args, kwargs = mock_sio.emit.call_args
        msg = args[1]
        assert msg["type"] == "RUN_UPDATE"
        assert msg["payload"]["run_id"] == 5
        assert msg["payload"]["progress"] == 50
        assert "timestamp" in msg

    @pytest.mark.asyncio
    async def test_task_update_envelope(self):
        from backend.realtime.socketio_server import broadcast_task_update

        mock_sio = _make_mock_sio()
        with patch("backend.realtime.socketio_server._sio", mock_sio):
            await broadcast_task_update(task_id=7, status="COMPLETED")

        mock_sio.emit.assert_called_once()
        args, kwargs = mock_sio.emit.call_args
        msg = args[1]
        assert msg["type"] == "TASK_UPDATE"
        assert msg["payload"]["task_id"] == 7
        assert "timestamp" in msg

    @pytest.mark.asyncio
    async def test_report_ready_envelope(self):
        from backend.realtime.socketio_server import broadcast_report_ready

        mock_sio = _make_mock_sio()
        with patch("backend.realtime.socketio_server._sio", mock_sio):
            await broadcast_report_ready(run_id=1, task_id=2)

        mock_sio.emit.assert_called_once()
        args, kwargs = mock_sio.emit.call_args
        msg = args[1]
        assert msg["type"] == "REPORT_READY"
        assert msg["payload"]["run_id"] == 1
        assert "timestamp" in msg
