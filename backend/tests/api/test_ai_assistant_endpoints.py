"""AI 助手 API 集成测试（testcontainers PG）。"""

import asyncio

import pytest

from backend.models.ai_assistant import (
    AiAssistantAction,
    AiAssistantConfig,
    AiChatMessage,
    AiChatSession,
)
from backend.models.audit import AuditLog
from backend.models.user import User
from backend.core.ai_security import encrypt_api_key


@pytest.fixture(autouse=True)
def _fernet_test_key(monkeypatch):
    monkeypatch.setenv("TESTING", "1")
    # 沙箱/宿主可能带 SOCKS 代理变量——测试内禁用，行为不随环境漂移
    for var in ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "http_proxy", "https_proxy"):
        monkeypatch.delenv(var, raising=False)


def _configure(db_session, *, enabled=True, with_key=True):
    cfg = db_session.get(AiAssistantConfig, 1)
    if cfg is None:
        cfg = AiAssistantConfig(id=1)
        db_session.add(cfg)
    cfg.base_url = "https://api.example.com/v1"
    cfg.model = "test-model"
    cfg.enabled = enabled
    cfg.api_key_encrypted = encrypt_api_key("sk-test-1234") if with_key else None
    db_session.commit()
    return cfg


class TestConfigEndpoints:
    def test_requires_admin(self, client, auth_headers):
        assert client.get("/api/v1/ai-assistant/config", headers=auth_headers).status_code == 403

    def test_masked_key_never_plaintext(self, client, admin_headers, db_session):
        _configure(db_session)
        resp = client.get("/api/v1/ai-assistant/config", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["api_key_masked"] and data["api_key_masked"].endswith("1234")
        assert "sk-test-1234" not in resp.text

    def test_put_empty_key_keeps_old(self, client, admin_headers, db_session):
        _configure(db_session)
        before = client.get("/api/v1/ai-assistant/config", headers=admin_headers).json()["data"]["api_key_masked"]
        resp = client.put(
            "/api/v1/ai-assistant/config",
            json={"base_url": "https://api2.example.com/v1"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        after = resp.json()["data"]["api_key_masked"]
        assert before == after
        assert resp.json()["data"]["base_url"] == "https://api2.example.com/v1"

    def test_put_audited(self, client, admin_headers, db_session):
        _configure(db_session)
        client.put(
            "/api/v1/ai-assistant/config",
            json={"temperature": 0.3},
            headers=admin_headers,
        )
        row = (
            db_session.query(AuditLog)
            .filter(AuditLog.action == "ai_assistant_config_update")
            .first()
        )
        assert row is not None

    def test_invalid_whitelist_entries_dropped(self, client, admin_headers, db_session):
        _configure(db_session)
        resp = client.put(
            "/api/v1/ai-assistant/config",
            json={"auto_approve_tools": ["reload_agent_config", "run_quality_gate", "test_notification_channel"]},
            headers=admin_headers,
        )
        assert resp.json()["data"]["auto_approve_tools"] == ["test_notification_channel"]

    def test_connection_test_refuses_fast(self, client, admin_headers, db_session):
        cfg = _configure(db_session)
        cfg.base_url = "http://127.0.0.1:9/v1"  # 不可达端口，连接立即拒绝
        db_session.commit()
        resp = client.post("/api/v1/ai-assistant/config/test-connection", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["ok"] is False
        assert data["error"]


class TestSessionsAndMessages:
    def test_session_isolation(self, client, auth_headers, admin_headers, db_session):
        admin_id = db_session.query(User).filter(User.role == "admin").first().id
        s = AiChatSession(user_id=admin_id, title="admin 的会话")
        db_session.add(s)
        db_session.commit()
        # 普通用户不可见 admin 的会话
        assert client.get(f"/api/v1/ai-assistant/sessions/{s.id}/messages", headers=auth_headers).status_code == 404
        assert client.delete(f"/api/v1/ai-assistant/sessions/{s.id}", headers=auth_headers).status_code == 404

    def test_send_message_without_config_409(self, client, auth_headers, db_session, monkeypatch):
        user_id = db_session.query(User).filter(User.role != "admin").first().id
        s = AiChatSession(user_id=user_id)
        db_session.add(s)
        db_session.commit()
        resp = client.post(
            f"/api/v1/ai-assistant/sessions/{s.id}/messages",
            json={"content": "你好"},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "ai_not_configured"

    def test_send_message_empty_content_422(self, client, auth_headers, db_session):
        _configure(db_session)
        user_id = db_session.query(User).filter(User.role != "admin").first().id
        s = AiChatSession(user_id=user_id)
        db_session.add(s)
        db_session.commit()
        resp = client.post(
            f"/api/v1/ai-assistant/sessions/{s.id}/messages",
            json={"content": "  "},
            headers=auth_headers,
        )
        assert resp.status_code == 422


class TestActionPermissions:
    def _proposed_action(self, db_session):
        user = db_session.query(User).filter(User.role != "admin").first()
        s = AiChatSession(user_id=user.id)
        db_session.add(s)
        db_session.flush()
        action = AiAssistantAction(
            session_id=s.id,
            tool_name="test_notification_channel",
            params={"channel_id": 1},
            status="proposed",
            requested_by_user_id=user.id,
        )
        db_session.add(action)
        db_session.commit()
        return action

    def test_non_admin_cannot_approve(self, client, auth_headers, db_session):
        action = self._proposed_action(db_session)
        resp = client.post(
            f"/api/v1/ai-assistant/actions/{action.id}/approve", headers=auth_headers
        )
        assert resp.status_code == 403

    def test_reject_flow_audited(self, client, admin_headers, auth_headers, db_session):
        action = self._proposed_action(db_session)
        resp = client.post(
            f"/api/v1/ai-assistant/actions/{action.id}/reject", headers=admin_headers
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "rejected"
        db_session.refresh(action)
        assert action.status == "rejected"
        assert (
            db_session.query(AuditLog)
            .filter(AuditLog.action == "ai_assistant_action_reject")
            .first()
        )

    def test_approve_requires_proposed(self, client, admin_headers, auth_headers, db_session):
        action = self._proposed_action(db_session)
        action.status = "succeeded"
        db_session.commit()
        resp = client.post(
            f"/api/v1/ai-assistant/actions/{action.id}/approve", headers=admin_headers
        )
        assert resp.status_code == 409


class TestTurnLoopWithFakeClient:
    """假 LLM 客户端驱动完整轮次（T0 直查 + T2 提案止轮 + 密钥不落消息）。"""

    def test_t0_query_and_final_answer(self, client, auth_headers, db_session, monkeypatch):
        _configure(db_session)
        from backend.services.ai_assistant import orchestrator as orch
        from backend.services.ai_assistant.llm_client import AssistantReply, ToolCallRequest

        replies = [
            AssistantReply(
                content="",
                tool_calls=[ToolCallRequest(id="c1", name="query_hosts", arguments={})],
            ),
            AssistantReply(content="平台当前没有注册主机。"),
        ]

        class FakeClient:
            def __init__(self, **kw):
                pass

            async def chat(self, messages, **kw):
                return replies.pop(0)

        monkeypatch.setattr(orch, "LlmClient", FakeClient)

        class _Shared:
            def __init__(self, s):
                self._s = s

            def __getattr__(self, name):
                return getattr(self._s, name)

            def close(self):
                pass

        monkeypatch.setattr(orch, "SessionLocal", lambda: _Shared(db_session))

        user = db_session.query(User).filter(User.role != "admin").first()
        s = AiChatSession(user_id=user.id, title="t")
        db_session.add(s)
        db_session.commit()

        asyncio.run(orch.ai_assistant_turn_task({}, session_id=s.id))

        msgs = (
            db_session.query(AiChatMessage)
            .filter(AiChatMessage.session_id == s.id)
            .order_by(AiChatMessage.id)
            .all()
        )
        roles = [m.role for m in msgs]
        assert "assistant" in roles and "tool" in roles
        tool_msg = next(m for m in msgs if m.role == "tool")
        assert "主机" in tool_msg.content
        final = [m for m in msgs if m.role == "assistant"][-1]
        assert final.status == "completed"
        # D7：任何消息与 meta 不得含 api key 明文
        for m in msgs:
            assert "sk-test-1234" not in (m.content or "")
            assert "sk-test-1234" not in str(m.meta or {})

    def test_t2_proposal_stops_turn(self, auth_headers, db_session, monkeypatch):
        _configure(db_session)
        from backend.services.ai_assistant import orchestrator as orch
        from backend.services.ai_assistant.llm_client import AssistantReply, ToolCallRequest

        class FakeClient:
            def __init__(self, **kw):
                pass

            async def chat(self, messages, **kw):
                return AssistantReply(
                    content="",
                    tool_calls=[
                        ToolCallRequest(
                            id="c1", name="test_notification_channel",
                            arguments={"channel_id": 1},
                        )
                    ],
                )

        monkeypatch.setattr(orch, "LlmClient", FakeClient)

        class _Shared:
            def __init__(self, s):
                self._s = s

            def __getattr__(self, name):
                return getattr(self._s, name)

            def close(self):
                pass

        monkeypatch.setattr(orch, "SessionLocal", lambda: _Shared(db_session))
        monkeypatch.setattr(orch, "_enqueue_continuation", lambda sid: None)

        user = db_session.query(User).filter(User.role != "admin").first()
        s = AiChatSession(user_id=user.id)
        db_session.add(s)
        db_session.commit()

        asyncio.run(orch.ai_assistant_turn_task({}, session_id=s.id))

        action = db_session.query(AiAssistantAction).filter_by(session_id=s.id).first()
        assert action is not None
        assert action.status == "proposed"
        assert action.tool_name == "test_notification_channel"
        # 助手消息挂上操作卡引用
        assistant_msgs = (
            db_session.query(AiChatMessage)
            .filter(AiChatMessage.session_id == s.id, AiChatMessage.role == "assistant")
            .all()
        )
        assert any((m.meta or {}).get("proposed_action_id") == action.id for m in assistant_msgs)

    def test_non_admin_cannot_reach_admin_only_tools(self, auth_headers, db_session, monkeypatch):
        """PR-Agent gate 越权修复回归：普通用户的轮次里，admin-only 工具
        不出现在 tools 载荷；直接点名调用走「未知工具」分支。"""
        _configure(db_session)
        from backend.services.ai_assistant import orchestrator as orch
        from backend.services.ai_assistant.llm_client import AssistantReply, ToolCallRequest

        seen_tools: list = []

        class FakeClient:
            def __init__(self, **kw):
                pass

            async def chat(self, messages, tools=None, **kw):
                seen_tools.append(tools)
                return AssistantReply(
                    content="",
                    tool_calls=[
                        ToolCallRequest(
                            id="c1", name="query_recent_audit_logs", arguments={}
                        )
                    ],
                )

        monkeypatch.setattr(orch, "LlmClient", FakeClient)

        class _Shared:
            def __init__(self, s):
                self._s = s

            def __getattr__(self, name):
                return getattr(self._s, name)

            def close(self):
                pass

        monkeypatch.setattr(orch, "SessionLocal", lambda: _Shared(db_session))

        user = db_session.query(User).filter(User.role != "admin").first()
        s = AiChatSession(user_id=user.id)
        db_session.add(s)
        db_session.commit()

        asyncio.run(orch.ai_assistant_turn_task({}, session_id=s.id))

        names = {t["function"]["name"] for t in (seen_tools[0] or [])}
        assert "query_recent_audit_logs" not in names
        tool_msg = (
            db_session.query(AiChatMessage)
            .filter(AiChatMessage.session_id == s.id, AiChatMessage.role == "tool")
            .first()
        )
        assert "不可用" in tool_msg.content

    def test_not_configured_fails_pending(self, auth_headers, db_session, monkeypatch):
        from backend.services.ai_assistant import orchestrator as orch

        class _Shared:
            def __init__(self, s):
                self._s = s

            def __getattr__(self, name):
                return getattr(self._s, name)

            def close(self):
                pass

        monkeypatch.setattr(orch, "SessionLocal", lambda: _Shared(db_session))

        user = db_session.query(User).filter(User.role != "admin").first()
        s = AiChatSession(user_id=user.id)
        db_session.add(s)
        db_session.flush()
        db_session.add(AiChatMessage(session_id=s.id, role="assistant", status="pending"))
        db_session.commit()

        asyncio.run(orch.ai_assistant_turn_task({}, session_id=s.id))

        pending = (
            db_session.query(AiChatMessage)
            .filter(AiChatMessage.session_id == s.id, AiChatMessage.status == "pending")
            .count()
        )
        assert pending == 0
