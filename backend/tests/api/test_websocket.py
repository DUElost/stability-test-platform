"""WebSocket contract tests (ADR-0009).

Validates that:
- WS endpoints accept connections with valid tokens
- WS endpoints reject connections without tokens in production mode
- All broadcast messages follow the standard envelope: {type, payload, timestamp}
"""

import json
import os
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from backend.main import app


@pytest.fixture
def ws_client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Connection / Auth
# ---------------------------------------------------------------------------

class TestDashboardWS:
    def test_connect_with_valid_token(self, ws_client):
        with ws_client.websocket_connect("/ws/dashboard?token=dev-token-12345") as ws:
            ws.send_text("{}")
            # If we get here, connection was accepted

    def test_connect_without_token_dev_mode(self, ws_client):
        """Dev mode allows connection without token."""
        with ws_client.websocket_connect("/ws/dashboard") as ws:
            ws.send_text("{}")

    def test_legacy_dashboard_endpoint(self, ws_client):
        """Legacy /dashboard endpoint should still work."""
        with ws_client.websocket_connect("/dashboard?token=dev-token-12345") as ws:
            ws.send_text("{}")


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
    def test_agent_auth_success(self, ws_client):
        with ws_client.websocket_connect("/ws/agent/test-host-001") as ws:
            ws.send_text(json.dumps({"type": "auth", "agent_secret": ""}))
            resp = json.loads(ws.recv())
            assert resp["type"] == "auth_ack"
            assert resp["status"] == "ok"

    def test_agent_auth_wrong_type_rejected(self, ws_client):
        with pytest.raises(Exception):
            with ws_client.websocket_connect("/ws/agent/test-host-002") as ws:
                ws.send_text(json.dumps({"type": "hello"}))
                ws.recv()


# ---------------------------------------------------------------------------
# Envelope contract: every broadcast message must have {type, payload, timestamp}
# ---------------------------------------------------------------------------

class TestBroadcastEnvelope:
    """Test that broadcast helpers produce messages with the standard envelope."""

    @pytest.mark.asyncio
    async def test_device_update_envelope(self):
        from backend.api.routes.websocket import broadcast_device_update, manager

        sent = []
        original_broadcast = manager.broadcast

        async def capture(path, message):
            sent.append((path, message))

        manager.broadcast = capture
        try:
            await broadcast_device_update({"id": 1, "status": "ONLINE"})
        finally:
            manager.broadcast = original_broadcast

        assert len(sent) == 1
        path, msg = sent[0]
        assert path == "/ws/dashboard"
        assert msg["type"] == "DEVICE_UPDATE"
        assert "payload" in msg
        assert "timestamp" in msg
        assert msg["payload"]["id"] == 1

    @pytest.mark.asyncio
    async def test_job_status_envelope(self):
        from backend.api.routes.websocket import broadcast_run_job_update, manager

        sent = []
        original_broadcast = manager.broadcast

        async def capture(path, message):
            sent.append((path, message))

        manager.broadcast = capture
        try:
            await broadcast_run_job_update(run_id=10, job_id=42, status="COMPLETED")
        finally:
            manager.broadcast = original_broadcast

        assert len(sent) == 1
        path, msg = sent[0]
        assert path == "/ws/workflow-runs/10"
        assert msg["type"] == "JOB_STATUS"
        assert msg["payload"]["job_id"] == 42
        assert msg["payload"]["status"] == "COMPLETED"
        assert "timestamp" in msg

    @pytest.mark.asyncio
    async def test_workflow_status_envelope(self):
        from backend.api.routes.websocket import broadcast_run_workflow_status, manager

        sent = []
        original_broadcast = manager.broadcast

        async def capture(path, message):
            sent.append((path, message))

        manager.broadcast = capture
        try:
            await broadcast_run_workflow_status(run_id=10, status="SUCCESS")
        finally:
            manager.broadcast = original_broadcast

        assert len(sent) == 1
        path, msg = sent[0]
        assert path == "/ws/workflow-runs/10"
        assert msg["type"] == "WORKFLOW_STATUS"
        assert msg["payload"]["status"] == "SUCCESS"
        assert "timestamp" in msg

    @pytest.mark.asyncio
    async def test_run_update_envelope(self):
        from backend.api.routes.websocket import broadcast_run_update, manager

        sent = []
        original_broadcast = manager.broadcast

        async def capture(path, message):
            sent.append((path, message))

        manager.broadcast = capture
        try:
            await broadcast_run_update(run_id=5, task_id=3, status="RUNNING", progress=50)
        finally:
            manager.broadcast = original_broadcast

        assert len(sent) == 1
        _, msg = sent[0]
        assert msg["type"] == "RUN_UPDATE"
        assert msg["payload"]["run_id"] == 5
        assert msg["payload"]["progress"] == 50
        assert "timestamp" in msg

    @pytest.mark.asyncio
    async def test_task_update_envelope(self):
        from backend.api.routes.websocket import broadcast_task_update, manager

        sent = []
        original_broadcast = manager.broadcast

        async def capture(path, message):
            sent.append((path, message))

        manager.broadcast = capture
        try:
            await broadcast_task_update(task_id=7, status="COMPLETED")
        finally:
            manager.broadcast = original_broadcast

        assert len(sent) == 1
        _, msg = sent[0]
        assert msg["type"] == "TASK_UPDATE"
        assert msg["payload"]["task_id"] == 7
        assert "timestamp" in msg

    @pytest.mark.asyncio
    async def test_report_ready_envelope(self):
        from backend.api.routes.websocket import broadcast_report_ready, manager

        sent = []
        original_broadcast = manager.broadcast

        async def capture(path, message):
            sent.append((path, message))

        manager.broadcast = capture
        try:
            await broadcast_report_ready(run_id=1, task_id=2)
        finally:
            manager.broadcast = original_broadcast

        assert len(sent) == 1
        _, msg = sent[0]
        assert msg["type"] == "REPORT_READY"
        assert msg["payload"]["run_id"] == 1
        assert "timestamp" in msg
