"""#302 — log_signal 死信清单 / 重放 RPC 端点。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from backend.realtime.socketio_server import AgentNotConnectedError, AgentRpcError


class TestListLogSignalDeadLetters:
    """GET /api/v1/hosts/{host_id}/log-signal-dead-letters"""

    def test_forbidden_for_non_admin(self, client, auth_headers):
        resp = client.get(
            "/api/v1/hosts/h1/log-signal-dead-letters",
            headers=auth_headers,
        )
        assert resp.status_code == 403

    def test_returns_dead_letters(self, client, admin_headers):
        with patch(
            "backend.realtime.socketio_server.call_agent_rpc",
            new=AsyncMock(return_value={
                "dead_letters": [
                    {"id": 1, "job_id": 7, "seq_no": 3, "attempts": 5},
                ],
            }),
        ) as mock_rpc:
            resp = client.get(
                "/api/v1/hosts/h1/log-signal-dead-letters?limit=50",
                headers=admin_headers,
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["host_id"] == "h1"
        assert data["dead_letters"][0]["id"] == 1
        mock_rpc.assert_awaited_once()
        args = mock_rpc.call_args[0]
        assert args[1] == "control"
        assert args[2]["command"] == "list_log_signal_dead_letters"
        assert args[2]["payload"] == {"limit": 50}

    def test_offline_host_returns_503(self, client, admin_headers):
        with patch(
            "backend.realtime.socketio_server.call_agent_rpc",
            new=AsyncMock(side_effect=AgentNotConnectedError("h1")),
        ):
            resp = client.get(
                "/api/v1/hosts/h1/log-signal-dead-letters",
                headers=admin_headers,
            )
        assert resp.status_code == 503

    def test_rpc_failure_returns_502(self, client, admin_headers):
        with patch(
            "backend.realtime.socketio_server.call_agent_rpc",
            new=AsyncMock(side_effect=AgentRpcError("timeout")),
        ):
            resp = client.get(
                "/api/v1/hosts/h1/log-signal-dead-letters",
                headers=admin_headers,
            )
        assert resp.status_code == 502


class TestReplayLogSignalDeadLetter:
    """POST /api/v1/hosts/{host_id}/log-signal-dead-letters/{row_id}/replay"""

    def test_forbidden_for_non_admin(self, client, auth_headers):
        resp = client.post(
            "/api/v1/hosts/h1/log-signal-dead-letters/3/replay",
            headers=auth_headers,
        )
        assert resp.status_code == 403

    def test_replay_ok(self, client, admin_headers):
        with patch(
            "backend.realtime.socketio_server.call_agent_rpc",
            new=AsyncMock(return_value={"ok": True, "row_id": 3}),
        ) as mock_rpc:
            resp = client.post(
                "/api/v1/hosts/h1/log-signal-dead-letters/3/replay",
                headers=admin_headers,
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["replayed"] is True
        assert data["row_id"] == 3
        args = mock_rpc.call_args[0]
        assert args[2]["command"] == "replay_log_signal_dead_letter"
        assert args[2]["payload"] == {"row_id": 3}

    def test_replay_missing_row_returns_404(self, client, admin_headers):
        with patch(
            "backend.realtime.socketio_server.call_agent_rpc",
            new=AsyncMock(return_value={"ok": False, "error": "invalid row_id"}),
        ):
            resp = client.post(
                "/api/v1/hosts/h1/log-signal-dead-letters/999/replay",
                headers=admin_headers,
            )
        assert resp.status_code == 404

    def test_offline_host_returns_503(self, client, admin_headers):
        with patch(
            "backend.realtime.socketio_server.call_agent_rpc",
            new=AsyncMock(side_effect=AgentNotConnectedError("h1")),
        ):
            resp = client.post(
                "/api/v1/hosts/h1/log-signal-dead-letters/3/replay",
                headers=admin_headers,
            )
        assert resp.status_code == 503
