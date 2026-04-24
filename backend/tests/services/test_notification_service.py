from types import SimpleNamespace

from backend.models.notification import EventType
from backend.services import notification_service as mod


class _FakeQuery:
    def __init__(self, rules):
        self._rules = rules

    def options(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self._rules


class _FakeSession:
    def __init__(self, rules):
        self._rules = rules
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def query(self, model):
        return _FakeQuery(self._rules)

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
