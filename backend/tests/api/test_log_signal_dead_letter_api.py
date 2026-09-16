"""#302 — log_signal 死信清单 / 重放 RPC 端点。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.realtime.socketio_server import AgentNotConnectedError, AgentRpcError


@pytest.fixture(autouse=True)
def _host_h1_exists(db_session):
    """#2383：本文件的 `host_id="h1"` 现在必须真实存在——端点先查存在性再打 RPC。

    过去这些用例拿一个库里不存在的 host 也能通过，恰恰是因为端点什么都没查：
    「主机不存在」与「Agent 没连接」被压成同一个 503（就是本单要修的缺陷）。
    """
    from datetime import datetime, timezone

    from backend.models.host import Host

    if db_session.get(Host, "h1") is None:
        db_session.add(Host(
            id="h1",
            hostname="dl-h1",
            name="dead-letter-host",
            ip="192.0.2.201",
            ip_address="192.0.2.201",
            status="ONLINE",
            last_heartbeat=datetime.now(timezone.utc),
        ))
        db_session.commit()


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

    def test_unknown_host_returns_404_before_touching_agent(
        self, client, admin_headers
    ):
        """#2383：主机不存在 → 404，且**根本不发起 RPC**（判别在 RPC 之前）。"""
        rpc = AsyncMock(return_value={"dead_letters": []})
        with patch("backend.realtime.socketio_server.call_agent_rpc", new=rpc):
            resp = client.get(
                "/api/v1/hosts/no-such-host/log-signal-dead-letters",
                headers=admin_headers,
            )
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == "host not found"
        rpc.assert_not_awaited()

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

    def test_replay_ok(self, client, admin_headers, db_session):
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
        self._assert_audit(db_session, "replayed")

    def test_replay_missing_row_returns_404(self, client, admin_headers, db_session):
        with patch(
            "backend.realtime.socketio_server.call_agent_rpc",
            new=AsyncMock(return_value={"ok": False, "error": "invalid row_id"}),
        ):
            resp = client.post(
                "/api/v1/hosts/h1/log-signal-dead-letters/999/replay",
                headers=admin_headers,
            )
        assert resp.status_code == 404
        self._assert_audit(db_session, "invalid row_id")

    def test_offline_host_returns_503(self, client, admin_headers, db_session):
        with patch(
            "backend.realtime.socketio_server.call_agent_rpc",
            new=AsyncMock(side_effect=AgentNotConnectedError("h1")),
        ):
            resp = client.post(
                "/api/v1/hosts/h1/log-signal-dead-letters/3/replay",
                headers=admin_headers,
            )
        assert resp.status_code == 503
        self._assert_audit(db_session, "agent_not_connected")

    def test_replay_unknown_host_returns_404_without_rpc_or_audit(
        self, client, admin_headers, db_session
    ):
        """写端点同理：打错 ID 不该被显示成「暂时不可用」并诱发重试。"""
        from backend.models.audit import AuditLog

        rpc = AsyncMock(return_value={"ok": True})
        with patch("backend.realtime.socketio_server.call_agent_rpc", new=rpc):
            resp = client.post(
                "/api/v1/hosts/no-such-host/log-signal-dead-letters/3/replay",
                headers=admin_headers,
            )
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == "host not found"
        rpc.assert_not_awaited()
        assert (
            db_session.query(AuditLog).filter_by(action="dead_letter_replay").count() == 0
        ), "没有发生过任何重放尝试，不得留下可归责审计"

    @staticmethod
    def _assert_audit(db_session, reason: str) -> None:
        # R02-F07（#907）：成功/失败均落审计（操作者+host+row 可还原）
        from backend.models.audit import AuditLog

        rows = (
            db_session.query(AuditLog)
            .filter(AuditLog.action == "dead_letter_replay")
            .all()
        )
        assert rows, "dead_letter_replay 审计行缺失"
        latest = rows[-1]
        assert latest.details["host_id"] == "h1"
        assert latest.details["reason"] == reason
        assert latest.username
