from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from backend.models.notification import (
    AlertRule,
    ChannelType,
    EventType,
    NotificationChannel,
    NotificationLog,
)
from backend.services import notification_service as mod


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
            raise RuntimeError("dingtalk business fail")

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
    assert delivery[str(bad_ch.id)]["status"] == "failed"

    sent.clear()
    with pytest.raises(mod.NotificationDeliveryError):
        mod.dispatch_notification(EventType.RUN_FAILED.value, ctx)
    # Only the previously failed channel is retried.
    assert sent == [bad_ch.id]
