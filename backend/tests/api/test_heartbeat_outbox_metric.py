"""Agent heartbeat outbox backlog metric (T-B4)."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.api.schemas.host import HeartbeatIn
from backend.api.routes.heartbeat import _process_heartbeat_with_db
from backend.models.host import Host


def test_heartbeat_extra_records_outbox_metrics(db_session, monkeypatch):
    recorded: list[tuple] = []

    def _record(host_id, outbox_type, count):
        recorded.append((host_id, outbox_type, count))

    monkeypatch.setattr(
        "backend.api.routes.heartbeat.record_agent_outbox_pending",
        _record,
    )

    host = Host(
        id="metric-host-1",
        hostname="agent-metric",
        status="ONLINE",
        last_heartbeat=datetime.now(timezone.utc),
    )
    db_session.add(host)
    db_session.commit()

    payload = HeartbeatIn(
        host_id="metric-host-1",
        status="ONLINE",
        extra={
            "terminal_outbox_pending": 3,
            "log_signal_outbox_pending": 7,
        },
    )
    _process_heartbeat_with_db(payload, db_session)
    db_session.commit()

    assert ("metric-host-1", "terminal", 3) in recorded
    assert ("metric-host-1", "log_signal", 7) in recorded
