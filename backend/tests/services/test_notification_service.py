from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.models.notification import (
    AlertRule,
    ChannelType,
    EventType,
    NotificationChannel,
    NotificationLog,
)
from backend.services import notification_service as mod
from backend.services.notification_delivery import (
    DeliveryOutcome,
    accepted,
    rejected_permanent,
    rejected_transient,
    unknown,
)


class _FakeQuery:
    def __init__(self, rules):
        self._rules = rules

    def options(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def all(self):
        return self._rules


class _FakeSession:
    def __init__(self, rules):
        self._rules = rules
        self.closed = False
        self._pending = None
        self._next_id = 1
        self._logs: dict[int, object] = {}

    def __enter__(self):
        self.closed = False
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def query(self, model):
        if model is NotificationLog:
            return _FakeQuery([])
        return _FakeQuery(self._rules)

    def add(self, obj):
        self._pending = obj

    def get(self, model, ident):
        if model is NotificationLog:
            return self._logs.get(ident) or (
                self._pending if getattr(self._pending, "id", None) == ident else None
            )
        return None

    def commit(self):
        if self._pending is not None and getattr(self._pending, "id", None) is None:
            self._pending.id = self._next_id
            self._next_id += 1
        if self._pending is not None and getattr(self._pending, "created_at", None) is None:
            self._pending.created_at = datetime.now(timezone.utc)
        if self._pending is not None and getattr(self._pending, "id", None) is not None:
            self._logs[self._pending.id] = self._pending

    def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = self._next_id
            self._next_id += 1
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(timezone.utc)
        self._logs[obj.id] = obj

    def close(self):
        self.closed = True


def test_dispatch_notification_closes_db_before_network_io(monkeypatch):
    channel = SimpleNamespace(
        id=7,
        enabled=True,
        type=SimpleNamespace(value="WEBHOOK"),
        config={"url": "http://example.invalid/webhook"},
    )
    rule = SimpleNamespace(id=11, filters={}, channel=channel)
    fake_session = _FakeSession([rule])

    monkeypatch.setattr(mod, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(mod, "_emit_notification_socketio", lambda *args, **kwargs: None)

    closed_states = []

    def fake_send_to_channel(sent_channel, message):
        assert sent_channel.id == channel.id
        assert sent_channel.type.value == channel.type.value
        assert sent_channel.config == channel.config
        assert "[Task Failed]" in message
        closed_states.append(fake_session.closed)
        return accepted("WEBHOOK")

    monkeypatch.setattr(mod, "send_to_channel", fake_send_to_channel)

    mod.dispatch_notification(
        EventType.RUN_FAILED.value,
        {
            "run_id": 42,
            "task_name": "demo-task",
            "task_type": "smoke",
            "device_serial": "ABC123",
            "error_message": "boom",
        },
    )

    assert closed_states == [True]


def test_dispatch_raises_when_channel_send_swallows_would_have_succeeded(monkeypatch):
    """#1117: real channel exception must surface (not only mocked SAQ path)."""
    channel = SimpleNamespace(
        id=7,
        enabled=True,
        type=SimpleNamespace(value="WEBHOOK"),
        config={"url": "http://example.invalid/webhook"},
    )
    rule = SimpleNamespace(id=11, filters={}, channel=channel)
    fake_session = _FakeSession([rule])
    monkeypatch.setattr(mod, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(mod, "_emit_notification_socketio", lambda *args, **kwargs: None)

    def boom(*_a, **_k):
        raise ConnectionError("webhook down")

    monkeypatch.setattr(mod, "send_to_channel", boom)

    with pytest.raises(mod.NotificationDeliveryError) as ei:
        mod.dispatch_notification(
            EventType.RUN_FAILED.value,
            {"run_id": 99, "task_name": "x", "device_serial": "s"},
        )
    assert ei.value.failed[0]["channel_id"] == 7
    assert "webhook down" in ei.value.failed[0]["error"]


def test_dispatch_skips_already_ok_channels_on_retry(db_session, monkeypatch):
    """#1117: retry must not resend channels already marked ok."""
    ok_ch = NotificationChannel(
        name="ok-hook",
        type=ChannelType.WEBHOOK,
        config={"url": "http://example.invalid/ok"},
        enabled=True,
    )
    bad_ch = NotificationChannel(
        name="bad-hook",
        type=ChannelType.WEBHOOK,
        config={"url": "http://example.invalid/bad"},
        enabled=True,
    )
    db_session.add_all([ok_ch, bad_ch])
    db_session.flush()
    db_session.add_all([
        AlertRule(
            name="r-ok",
            event_type=EventType.RUN_FAILED,
            channel_id=ok_ch.id,
            enabled=True,
        ),
        AlertRule(
            name="r-bad",
            event_type=EventType.RUN_FAILED,
            channel_id=bad_ch.id,
            enabled=True,
        ),
    ])
    db_session.commit()

    monkeypatch.setattr(mod, "_emit_notification_socketio", lambda *a, **k: None)

    sent: list[int] = []

    def selective_send(channel, message):
        sent.append(channel.id)
        if channel.id == bad_ch.id:
            # #1167 P2：永久拒绝不再触发 SAQ 重试——用 TRANSIENT 保持
            # 「可重试失败抛出 NotificationDeliveryError」的既有语义
            return rejected_transient("dingtalk transient fail")
        return accepted("WEBHOOK")

    monkeypatch.setattr(mod, "send_to_channel", selective_send)

    ctx = {"run_id": 501, "task_id": 7, "device_serial": "D1", "task_name": "t"}

    with pytest.raises(mod.NotificationDeliveryError):
        mod.dispatch_notification(EventType.RUN_FAILED.value, ctx)
    assert sent == [ok_ch.id, bad_ch.id]

    log = (
        db_session.query(NotificationLog)
        .filter(NotificationLog.event_type == EventType.RUN_FAILED.value)
        .order_by(NotificationLog.id.desc())
        .first()
    )
    assert log is not None
    delivery = (log.context or {}).get("channel_delivery") or {}
    assert delivery[str(ok_ch.id)]["status"] == "ok"
    assert delivery[str(ok_ch.id)]["outcome"] == DeliveryOutcome.ACCEPTED.value
    assert delivery[str(bad_ch.id)]["status"] == "failed"
    assert delivery[str(bad_ch.id)]["outcome"] == DeliveryOutcome.REJECTED_TRANSIENT.value

    sent.clear()
    with pytest.raises(mod.NotificationDeliveryError):
        mod.dispatch_notification(EventType.RUN_FAILED.value, ctx)
    # Only the previously failed channel is retried.
    assert sent == [bad_ch.id]


def test_send_dingtalk_business_errcode_is_permanent(monkeypatch):
    """#1120 + #1167 D2: HTTP 200 with errcode!=0 → REJECTED_PERMANENT（不重试）。"""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"errcode": 310000, "errmsg": "sign not match"}
    monkeypatch.setattr(mod.requests, "post", MagicMock(return_value=resp))

    result = mod._send_dingtalk("https://oapi.dingtalk.com/robot/send?access_token=x", "", "hi")
    assert result.outcome is DeliveryOutcome.REJECTED_PERMANENT
    assert not result.retryable
    assert "errcode=310000" in result.detail


def test_send_dingtalk_ok_when_errcode_zero(monkeypatch):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"errcode": 0, "errmsg": "ok"}
    monkeypatch.setattr(mod.requests, "post", MagicMock(return_value=resp))

    result = mod._send_dingtalk("https://oapi.dingtalk.com/robot/send?access_token=x", "", "hi")
    assert result.accepted


def test_send_to_channel_dingtalk_surfaces_business_error(monkeypatch):
    """Test-channel API path（#1167 D1/D9）：业务失败归一化为
    REJECTED_PERMANENT 结果，路由依此返回 502。"""
    channel = SimpleNamespace(
        type=SimpleNamespace(value="DINGTALK"),
        config={"url": "https://oapi.dingtalk.com/robot/send?access_token=x", "secret": ""},
    )
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"errcode": 40035, "errmsg": "缺少参数 token"}
    monkeypatch.setattr(mod.requests, "post", MagicMock(return_value=resp))

    result = mod.send_to_channel(channel, "This is a test notification from Stability Test Platform.")
    assert result.outcome is DeliveryOutcome.REJECTED_PERMANENT
    assert "errcode=40035" in result.detail


def test_webhook_http_error_summary_redacts_credentials(monkeypatch):
    """R13-F02 (#1214): 带 token 的 webhook 失败不得把 URL/凭据带进错误文本。"""
    resp = MagicMock()
    resp.status_code = 401
    resp.reason = "Unauthorized"
    monkeypatch.setattr(mod.requests, "post", MagicMock(return_value=resp))

    result = mod._send_webhook(
        "https://hooks.example.com/notify?access_token=SUPERSECRET&channel=alerts",
        "hi",
    )

    assert result.outcome is DeliveryOutcome.REJECTED_PERMANENT  # 401 = 配置/鉴权错
    text = result.detail
    assert "SUPERSECRET" not in text
    assert "access_token" not in text
    assert "hooks.example.com" not in text
    assert "HTTP 401" in text


def test_webhook_http_error_reason_is_redacted(monkeypatch):
    """Even a smuggled URL inside the reason phrase is redacted (#1214)."""
    resp = MagicMock()
    resp.status_code = 500
    resp.reason = "caused by https://hooks.example.com/x?token=LEAKME"
    monkeypatch.setattr(mod.requests, "post", MagicMock(return_value=resp))

    result = mod._send_webhook("https://hooks.example.com/x", "hi")

    assert result.outcome is DeliveryOutcome.REJECTED_TRANSIENT  # 500 = 瞬时
    assert "LEAKME" not in result.detail


# ── #1122：SMTP 网络超时 + 队列满拒绝不外溢 ──────────────────────────────


def test_send_email_passes_explicit_timeout(monkeypatch):
    """SMTP 连接必须带 deadline——无超时会挂死通知线程。"""
    calls = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            calls["host"], calls["port"], calls["timeout"] = host, port, timeout

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            calls["starttls"] = True

        def login(self, user, password):
            pass

        def sendmail(self, frm, to, body):
            pass

    monkeypatch.setattr(mod, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(mod, "SMTP_PORT", 587)
    monkeypatch.setattr(mod.smtplib, "SMTP", FakeSMTP)
    mod._send_email("ops@example.com", "STP", "hello")

    assert calls["host"] == "smtp.example.com"
    assert calls["timeout"] == mod.SMTP_TIMEOUT_SECONDS
    assert calls["timeout"] >= 1


def test_dispatch_notification_async_swallows_queue_full(monkeypatch):
    """队列满被拒绝时丢弃并告警，绝不向调用方外溢（fire-and-forget 契约）。"""
    from backend.core.thread_pool import PoolQueueFullError

    def full_submit(fn, *args, **kwargs):
        raise PoolQueueFullError("background pool queue full")

    monkeypatch.setattr(
        "backend.core.thread_pool.submit", full_submit,
    )
    # 不应抛出
    mod.dispatch_notification_async("system_alert", {"run_id": 1})


# ── #1167 P2（D2/D5）：永久拒绝记录不重试，可重试失败才抛 ────────────────


def test_dispatch_permanent_failure_records_without_retry_raise(monkeypatch):
    """REJECTED_PERMANENT（配置/鉴权错）如实落投递事实，但不触发 SAQ 重试。"""
    channel = SimpleNamespace(
        id=8,
        enabled=True,
        type=SimpleNamespace(value="DINGTALK"),
        config={"url": "http://example.invalid/hook"},
    )
    rule = SimpleNamespace(id=12, filters={}, channel=channel)
    fake_session = _FakeSession([rule])
    monkeypatch.setattr(mod, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(mod, "_emit_notification_socketio", lambda *a, **k: None)

    monkeypatch.setattr(
        mod, "send_to_channel",
        lambda ch, msg: rejected_permanent("DingTalk API error errcode=310000"),
    )

    persisted: dict = {}
    monkeypatch.setattr(
        mod, "_persist_channel_delivery",
        lambda log_id, delivery: persisted.update(delivery),
    )

    # 不抛 —— 永久拒绝重试无意义（D5）
    mod.dispatch_notification(
        EventType.RUN_FAILED.value,
        {"run_id": 77, "task_name": "x", "device_serial": "s"},
    )
    rec = persisted.get("8")
    assert rec is not None
    assert rec["status"] == "failed"
    assert rec["outcome"] == DeliveryOutcome.REJECTED_PERMANENT.value


def test_dispatch_retryable_failure_raises_with_outcome(monkeypatch):
    """REJECTED_TRANSIENT / UNKNOWN 抛 NotificationDeliveryError（供 SAQ 重试），
    failed 明细携带 outcome/retryable。"""
    channel = SimpleNamespace(
        id=9,
        enabled=True,
        type=SimpleNamespace(value="WEBHOOK"),
        config={"url": "http://example.invalid/hook"},
    )
    rule = SimpleNamespace(id=13, filters={}, channel=channel)
    fake_session = _FakeSession([rule])
    monkeypatch.setattr(mod, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(mod, "_emit_notification_socketio", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_persist_channel_delivery", lambda log_id, delivery: None)
    monkeypatch.setattr(
        mod, "send_to_channel",
        lambda ch, msg: unknown("timeout: read timeout"),
    )

    with pytest.raises(mod.NotificationDeliveryError) as ei:
        mod.dispatch_notification(
            EventType.RUN_FAILED.value,
            {"run_id": 78, "task_name": "x", "device_serial": "s"},
        )
    assert ei.value.failed[0]["outcome"] == DeliveryOutcome.UNKNOWN.value
    assert ei.value.failed[0]["retryable"] is True


# ── #1167 P3（D4/D7）：SAQ 唯一 retry owner + 降级路径 ─────────────────────


def test_dispatch_async_enqueues_saq(monkeypatch):
    """入队成功 → 不再走线程池；key 含事件身份（去重键）。"""
    from backend.tasks import saq_worker as sw

    captured: dict = {}

    def fake_enqueue(task_name, **kwargs):
        captured["task"] = task_name
        captured.update(kwargs)
        return True

    monkeypatch.setattr(sw, "enqueue_sync", fake_enqueue)
    pool_called = {"v": False}
    monkeypatch.setattr(
        "backend.core.thread_pool.submit",
        lambda *a, **k: pool_called.update(v=True),
    )

    mod.dispatch_notification_async("RUN_FAILED", {"run_id": 42, "device_serial": "D"})

    assert captured["task"] == "send_notification_task"
    assert captured["key"] == "notif:RUN_FAILED:42:D"
    assert captured["retries"] == 3
    assert captured["event_type"] == "RUN_FAILED"
    assert pool_called["v"] is False, "入队成功不得再走线程池"


def test_dispatch_async_falls_back_to_pool_when_saq_unavailable(monkeypatch):
    """SAQ 未运行（enqueue 返回 False）→ 降级 best-effort 线程池，不外溢。"""
    from backend.tasks import saq_worker as sw

    monkeypatch.setattr(sw, "enqueue_sync", lambda *a, **k: False)
    submitted: dict = {}
    monkeypatch.setattr(
        "backend.core.thread_pool.submit",
        lambda fn, *a, **k: submitted.update(fn=fn),
    )

    mod.dispatch_notification_async("DEVICE_OFFLINE", {"device_serial": "D"})

    assert "fn" in submitted, "降级路径必须提交线程池"


def test_dispatch_async_swallows_enqueue_exception(monkeypatch):
    """enqueue 抛异常（Redis 故障）→ 记日志 + 降级线程池，不向调用方外溢。"""
    from backend.tasks import saq_worker as sw

    def boom(*a, **k):
        raise RuntimeError("redis down")

    monkeypatch.setattr(sw, "enqueue_sync", boom)
    submitted: dict = {}
    monkeypatch.setattr(
        "backend.core.thread_pool.submit",
        lambda fn, *a, **k: submitted.update(fn=fn),
    )

    mod.dispatch_notification_async("RUN_COMPLETED", {"run_id": 1})

    assert "fn" in submitted


# ── #1167 P4（D3/D6）：事实表落库 + 权威读取 + 每通道 deadline ─────────────


def test_channel_deadline_env_override(monkeypatch):
    assert mod._channel_deadline("STP_NOTIFY_NO_SUCH_TIMEOUT_S", 10.0) == 10.0
    monkeypatch.setenv("STP_NOTIFY_TEST_TIMEOUT_S", "3.5")
    assert mod._channel_deadline("STP_NOTIFY_TEST_TIMEOUT_S", 10.0) == 3.5
    monkeypatch.setenv("STP_NOTIFY_TEST_TIMEOUT_S", "not-a-number")
    assert mod._channel_deadline("STP_NOTIFY_TEST_TIMEOUT_S", 10.0) == 10.0
    monkeypatch.setenv("STP_NOTIFY_TEST_TIMEOUT_S", "0")
    assert mod._channel_deadline("STP_NOTIFY_TEST_TIMEOUT_S", 10.0) == 1.0


def test_delivery_facts_persist_and_attempt_count_increments(db_session, monkeypatch):
    """真实 dispatch：首次插入事实行，重试在原行累加 attempt_count。"""
    from backend.models.notification import (
        AlertRule, ChannelType, NotificationChannel, NotificationDelivery,
    )
    from backend.services.notification_delivery import unknown

    ch = NotificationChannel(
        name="p4-facts", type=ChannelType.WEBHOOK,
        config={"url": "http://p4.test/x"}, enabled=True,
    )
    db_session.add(ch)
    db_session.flush()
    db_session.add(AlertRule(
        name="p4-facts-rule", event_type=EventType.RUN_FAILED,
        channel_id=ch.id, enabled=True,
    ))
    db_session.commit()

    monkeypatch.setattr(mod, "_emit_notification_socketio", lambda *a, **k: None)
    outcomes = [unknown("timeout"), accepted("WEBHOOK", "HTTP 200")]
    monkeypatch.setattr(mod, "send_to_channel", lambda c, m: outcomes.pop(0))

    ctx = {"run_id": 1201, "task_id": 9, "device_serial": "P4D", "task_name": "t"}

    with pytest.raises(mod.NotificationDeliveryError):
        mod.dispatch_notification(EventType.RUN_FAILED.value, ctx)
    db_session.expire_all()
    row = db_session.query(NotificationDelivery).filter_by(channel_id=ch.id).one()
    assert row.state == "retrying"
    assert row.outcome == "UNKNOWN"
    assert row.attempt_count == 1

    mod.dispatch_notification(EventType.RUN_FAILED.value, ctx)
    db_session.expire_all()
    row = db_session.query(NotificationDelivery).filter_by(channel_id=ch.id).one()
    assert row.state == "accepted"
    assert row.outcome == "ACCEPTED"
    assert row.attempt_count == 2


def test_fact_table_is_authoritative_for_idempotency(db_session, monkeypatch):
    """事实表行 ACCEPTED → 跳过该通道（即使 JSONB 无记录），不再发。"""
    from backend.models.notification import (
        AlertRule, ChannelType, NotificationChannel,
        NotificationDelivery, NotificationLog,
    )

    ch = NotificationChannel(
        name="p4-auth", type=ChannelType.WEBHOOK,
        config={"url": "http://p4.test/auth"}, enabled=True,
    )
    db_session.add(ch)
    db_session.flush()
    db_session.add(AlertRule(
        name="p4-auth-rule", event_type=EventType.RUN_FAILED,
        channel_id=ch.id, enabled=True,
    ))
    ctx = {"run_id": 1202, "task_id": 9, "device_serial": "P4E", "task_name": "t"}
    log = NotificationLog(
        source=mod.NotificationSource.PLATFORM, event_type=EventType.RUN_FAILED.value,
        severity=mod.NotificationSeverity.WARNING, title="t", message="m",
        context=ctx,  # 注意：无 channel_delivery（模拟 P4 后只写表）
    )
    db_session.add(log)
    db_session.flush()
    db_session.add(NotificationDelivery(
        notification_log_id=log.id, channel_id=ch.id, channel_type="WEBHOOK",
        state="accepted", outcome="ACCEPTED", attempt_count=1,
    ))
    db_session.commit()

    monkeypatch.setattr(mod, "_emit_notification_socketio", lambda *a, **k: None)
    sent: list = []
    monkeypatch.setattr(mod, "send_to_channel", lambda c, m: sent.append(c.id) or accepted())

    mod.dispatch_notification(EventType.RUN_FAILED.value, ctx)
    assert sent == [], "事实表 ACCEPTED 的通道不得重发"


def test_legacy_jsonb_fallback_still_skips(db_session, monkeypatch):
    """P4 历史日志（无表行）回落 JSONB：status ok 的通道仍不重发。"""
    from backend.models.notification import (
        AlertRule, ChannelType, NotificationChannel, NotificationLog,
    )

    ch = NotificationChannel(
        name="p4-legacy", type=ChannelType.WEBHOOK,
        config={"url": "http://p4.test/legacy"}, enabled=True,
    )
    db_session.add(ch)
    db_session.flush()
    db_session.add(AlertRule(
        name="p4-legacy-rule", event_type=EventType.RUN_COMPLETED,
        channel_id=ch.id, enabled=True,
    ))
    ctx = {"run_id": 1203, "task_id": 9, "device_serial": "P4F", "task_name": "t"}
    db_session.add(NotificationLog(
        source=mod.NotificationSource.PLATFORM, event_type=EventType.RUN_COMPLETED.value,
        severity=mod.NotificationSeverity.INFO, title="t", message="m",
        context={**ctx, "channel_delivery": {str(ch.id): {"status": "ok"}}},
    ))
    db_session.commit()

    monkeypatch.setattr(mod, "_emit_notification_socketio", lambda *a, **k: None)
    sent: list = []
    monkeypatch.setattr(mod, "send_to_channel", lambda c, m: sent.append(c.id) or accepted())

    mod.dispatch_notification(EventType.RUN_COMPLETED.value, ctx)
    assert sent == [], "历史 JSONB 记录回落判定不得重发"
