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


class TestReviewRound2Fixes:
    def test_pending_placeholder_converges_on_success(self, auth_headers, db_session, monkeypatch):
        """H1 回归：轮次成功产出真实回复后，pending 占位必须收口（删除），
        不得残留——否则前端无限轮询 + 「思考中」气泡永挂（生产曾复现 8 条）。"""
        _configure(db_session)
        from backend.services.ai_assistant import orchestrator as orch
        from backend.services.ai_assistant.llm_client import AssistantReply

        class FakeClient:
            def __init__(self, **kw):
                pass

            async def chat(self, messages, **kw):
                return AssistantReply(content="平台正常。")

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
        db_session.flush()
        db_session.add(
            AiChatMessage(session_id=s.id, role="assistant", content="", status="pending")
        )
        db_session.commit()

        asyncio.run(orch.ai_assistant_turn_task({}, session_id=s.id))

        pending = (
            db_session.query(AiChatMessage)
            .filter(
                AiChatMessage.session_id == s.id,
                AiChatMessage.role == "assistant",
                AiChatMessage.status == "pending",
            )
            .count()
        )
        assert pending == 0
        assistants = (
            db_session.query(AiChatMessage)
            .filter(AiChatMessage.session_id == s.id, AiChatMessage.role == "assistant")
            .all()
        )
        assert len(assistants) == 1 and assistants[0].content == "平台正常。"

    def test_plan_runs_status_enum_feedback(self, db_session):
        """M1 回归：非法状态值把合法列表回给模型（零枚举猜测）。"""
        from backend.services.ai_assistant.tools import ToolValidationError, execute_query

        with pytest.raises(ToolValidationError) as ei:
            execute_query(db_session, "query_plan_runs", {"status": "PENDING"})
        assert "RUNNING" in str(ei.value)

    def test_admin_cannot_read_others_session(self, client, admin_headers, auth_headers, db_session):
        """M4 回归：会话严格隔离，admin 也无跨用户通道（404 语义）。"""
        user_id = db_session.query(User).filter(User.role != "admin").first().id
        s = AiChatSession(user_id=user_id)
        db_session.add(s)
        db_session.commit()
        resp = client.get(f"/api/v1/ai-assistant/sessions/{s.id}/messages", headers=admin_headers)
        assert resp.status_code == 404

    def test_proposed_action_not_executable(self, auth_headers, db_session, monkeypatch):
        """Low-1 回归：执行闸门只认 approved——proposed 不可绕过审批直启。"""
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
        action = AiAssistantAction(
            session_id=s.id, tool_name="test_notification_channel", params={},
            status="proposed", requested_by_user_id=user.id,
        )
        db_session.add(action)
        db_session.commit()

        orch.execute_action(action.id)
        db_session.refresh(action)
        assert action.status == "proposed"


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

    def test_history_skips_empty_assistant_placeholder(self, auth_headers, db_session, monkeypatch):
        """线上 400 回归：send_message 的 pending 占位（content 空、无 tool_calls）
        不得进入 LLM 历史——严格供应商拒绝 "content or tool_calls must be set"。"""
        _configure(db_session)
        from backend.services.ai_assistant import orchestrator as orch
        from backend.services.ai_assistant.llm_client import AssistantReply

        captured: list = []

        class FakeClient:
            def __init__(self, **kw):
                pass

            async def chat(self, messages, **kw):
                captured.append(messages)
                return AssistantReply(content="你好，平台一切正常。")

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
        db_session.flush()
        db_session.add(AiChatMessage(session_id=s.id, role="user", content="你好"))
        # 复现线上：pending 占位先于轮次存在
        db_session.add(
            AiChatMessage(session_id=s.id, role="assistant", content="", status="pending")
        )
        # 一条已失败且内容为空的 assistant 消息（同样不得进历史）
        db_session.add(
            AiChatMessage(
                session_id=s.id, role="assistant", content="", status="failed",
                meta={"error": "llm unexpected status 400"},
            )
        )
        db_session.commit()

        asyncio.run(orch.ai_assistant_turn_task({}, session_id=s.id))

        llm_messages = captured[0]
        assistant_entries = [m for m in llm_messages if m["role"] == "assistant"]
        # 仅含轮内产生的真实回复，无空 content 且无 tool_calls 的条目
        for entry in assistant_entries:
            assert entry.get("content") or entry.get("tool_calls"), entry
        # 最终回复已落库
        final = (
            db_session.query(AiChatMessage)
            .filter(AiChatMessage.session_id == s.id, AiChatMessage.role == "assistant")
            .all()
        )
        assert any(m.content == "你好，平台一切正常。" for m in final)

    def test_action_receipt_injected_as_user_role(self, auth_headers, db_session, monkeypatch):
        """动作完成回执（tool 消息、无 tool_call_id）注入为 user 角色，
        避免「tool 消息无前置 tool_calls」的严格校验拒绝。"""
        _configure(db_session)
        from backend.services.ai_assistant import orchestrator as orch
        from backend.services.ai_assistant.llm_client import AssistantReply

        captured: list = []

        class FakeClient:
            def __init__(self, **kw):
                pass

            async def chat(self, messages, **kw):
                captured.append(messages)
                return AssistantReply(content="收到，任务已完成。")

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
        db_session.flush()
        # 模拟动作完成后的回执（orchestrator._finalize_action 落的形态）
        db_session.add(
            AiChatMessage(
                session_id=s.id, role="tool", tool_call_id=None,
                content="run_quality_gate → SUCCESS（exit=0）",
            )
        )
        db_session.commit()

        asyncio.run(orch.ai_assistant_turn_task({}, session_id=s.id))

        llm_messages = captured[0]
        assert any(
            m["role"] == "user" and "[执行回执] run_quality_gate" in (m.get("content") or "")
            for m in llm_messages
        )
        assert not any(
            m["role"] == "tool" and not m.get("tool_call_id") for m in llm_messages
        )

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


class TestContinuationVisibility:
    """续轮汇报可见性：占位是前端「助手仍欠一条回复」的唯一轮询信号。

    删早了 → UI 停轮，动作完成后的汇报只落库不上屏（全局
    refetchOnWindowFocus=false，无兜底刷新）。
    """

    @staticmethod
    def _shared_session(monkeypatch, orch, db_session):
        class _Shared:
            def __init__(self, s):
                self._s = s

            def __getattr__(self, name):
                return getattr(self._s, name)

            def close(self):
                pass

        monkeypatch.setattr(orch, "SessionLocal", lambda: _Shared(db_session))

    @staticmethod
    def _fake_llm(monkeypatch, orch, replies):
        class FakeClient:
            def __init__(self, **kw):
                pass

            async def chat(self, messages, **kw):
                return replies.pop(0)

        monkeypatch.setattr(orch, "LlmClient", FakeClient)

    def _session_with_placeholder(self, db_session):
        user = db_session.query(User).filter(User.role != "admin").first()
        s = AiChatSession(user_id=user.id)
        db_session.add(s)
        db_session.flush()
        db_session.add(AiChatMessage(session_id=s.id, role="user", content="跑一下 agent 测试"))
        db_session.add(
            AiChatMessage(session_id=s.id, role="assistant", content="", status="pending")
        )
        db_session.commit()
        return s

    def _pending_count(self, db_session, session_id):
        return (
            db_session.query(AiChatMessage)
            .filter(
                AiChatMessage.session_id == session_id,
                AiChatMessage.role == "assistant",
                AiChatMessage.status == "pending",
            )
            .count()
        )

    def test_auto_action_keeps_placeholder_until_continuation(
        self, auth_headers, db_session, monkeypatch
    ):
        """T1 自动执行止轮时占位必须保留——续轮汇报靠它驱动前端轮询。"""
        _configure(db_session)
        from backend.services.ai_assistant import orchestrator as orch
        from backend.services.ai_assistant.llm_client import AssistantReply, ToolCallRequest

        self._fake_llm(
            monkeypatch, orch,
            [AssistantReply(
                content="",
                tool_calls=[ToolCallRequest(id="c1", name="run_agent_tests", arguments={})],
            )],
        )
        self._shared_session(monkeypatch, orch, db_session)

        def _fake_execute(action_id):
            action = db_session.get(AiAssistantAction, action_id)
            action.status = "running"  # RunConsole 已起，等 on_complete 续轮
            db_session.commit()

        monkeypatch.setattr(orch, "execute_action", _fake_execute)

        s = self._session_with_placeholder(db_session)
        asyncio.run(orch.ai_assistant_turn_task({}, session_id=s.id))

        action = db_session.query(AiAssistantAction).filter_by(session_id=s.id).one()
        assert action.status == "running"
        tool_msg = (
            db_session.query(AiChatMessage)
            .filter(AiChatMessage.session_id == s.id, AiChatMessage.role == "tool")
            .one()
        )
        assert "已开始执行" in tool_msg.content
        # 关键断言：占位仍在（否则 UI 停轮，续轮汇报不上屏）
        assert self._pending_count(db_session, s.id) == 1

    def test_inline_terminal_action_reported_in_same_turn(
        self, auth_headers, db_session, monkeypatch
    ):
        """内联出终态（服务工具/RunKeyBusy/spawn 失败）：同 key 续轮会被 SAQ
        静默丢弃，必须本轮把真实结果喂回模型，且不得谎报「已开始执行」。"""
        _configure(db_session)
        from backend.services.ai_assistant import orchestrator as orch
        from backend.services.ai_assistant.llm_client import AssistantReply, ToolCallRequest

        self._fake_llm(
            monkeypatch, orch,
            [
                AssistantReply(
                    content="",
                    tool_calls=[ToolCallRequest(id="c1", name="run_agent_tests", arguments={})],
                ),
                AssistantReply(content="启动失败了：同类任务正在运行。"),
            ],
        )
        self._shared_session(monkeypatch, orch, db_session)

        def _fake_execute(action_id):
            action = db_session.get(AiAssistantAction, action_id)
            action.status = "failed"
            action.result_summary = "同 run_key 任务正在运行，请稍后重试"
            db_session.commit()

        monkeypatch.setattr(orch, "execute_action", _fake_execute)

        s = self._session_with_placeholder(db_session)
        asyncio.run(orch.ai_assistant_turn_task({}, session_id=s.id))

        tool_msg = (
            db_session.query(AiChatMessage)
            .filter(AiChatMessage.session_id == s.id, AiChatMessage.role == "tool")
            .one()
        )
        assert "已开始执行" not in tool_msg.content
        assert "已结束（failed）" in tool_msg.content
        assert "同 run_key" in tool_msg.content
        # 本轮继续跑完并产出终答，占位随之收口
        final = (
            db_session.query(AiChatMessage)
            .filter(
                AiChatMessage.session_id == s.id,
                AiChatMessage.role == "assistant",
                AiChatMessage.status == "completed",
            )
            .order_by(AiChatMessage.id.desc())
            .first()
        )
        assert final is not None and "启动失败" in final.content
        assert self._pending_count(db_session, s.id) == 0

    def test_continuation_enqueue_failure_marks_placeholder_failed(
        self, auth_headers, db_session, monkeypatch
    ):
        """续轮入队失败不得留悬挂占位——标 failed 让用户看见而不是永远转圈。"""
        from backend.services.ai_assistant import orchestrator as orch

        self._shared_session(monkeypatch, orch, db_session)
        monkeypatch.setattr(
            "backend.tasks.saq_worker.enqueue_sync", lambda *a, **kw: False
        )

        s = self._session_with_placeholder(db_session)
        orch._enqueue_continuation(s.id)

        placeholder = (
            db_session.query(AiChatMessage)
            .filter(
                AiChatMessage.session_id == s.id,
                AiChatMessage.role == "assistant",
            )
            .one()
        )
        assert placeholder.status == "failed"
        assert "入队失败" in (placeholder.meta or {}).get("error", "")

    def test_approve_creates_placeholder_for_report(
        self, client, admin_headers, auth_headers, db_session, monkeypatch
    ):
        """审批放行后的汇报同样经续轮——approve 时就落占位，
        前端 invalidate messages 后据此恢复轮询。"""
        monkeypatch.setattr(
            "backend.api.routes.ai_assistant.execute_action", lambda action_id: None
        )
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

        resp = client.post(
            f"/api/v1/ai-assistant/actions/{action.id}/approve", headers=admin_headers
        )
        assert resp.status_code == 200
        assert self._pending_count(db_session, s.id) == 1
