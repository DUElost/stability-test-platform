"""#3219: Agent cumulative timing buckets reach /metrics without stale host rows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.agent.heartbeat_timing import HeartbeatTiming
from backend.models.enums import HostStatus
from backend.models.host import Host


def _host(db, host_id: str, snapshot: dict) -> Host:
    now = datetime.now(timezone.utc)
    host = Host(
        id=host_id, hostname=host_id, status=HostStatus.ONLINE.value,
        ip="10.44.99.21", ssh_user="root", ssh_port=22,
        extra={"heartbeat_timing": snapshot}, last_heartbeat=now, created_at=now,
    )
    db.add(host)
    db.commit()
    return host


def test_cumulative_buckets_are_exposed_and_removed_on_retirement(
    client, db_session, monkeypatch,
):
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    timing = HeartbeatTiming()
    timing.observe("tick_total", 0.1)
    timing.observe("tick_total", 2.0)
    snapshot = timing.snapshot(slow_due=25, disk_due=8)
    host = _host(db_session, "timing-h1", snapshot)

    body = client.get("/metrics").text
    assert 'stability_agent_heartbeat_phase_seconds_bucket{host_id="timing-h1",le="0.1",phase="tick_total"} 1.0' in body
    assert 'stability_agent_heartbeat_phase_seconds_bucket{host_id="timing-h1",le="2.5",phase="tick_total"} 2.0' in body
    assert 'stability_agent_heartbeat_phase_seconds_count{host_id="timing-h1",phase="tick_total"} 2.0' in body
    assert 'stability_agent_heartbeat_probe_due{host_id="timing-h1",kind="slow"} 25.0' in body
    assert 'stability_agent_heartbeat_probe_due{host_id="timing-h1",kind="disk"} 8.0' in body

    host.retired_at = datetime.now(timezone.utc)
    db_session.commit()
    body = client.get("/metrics").text
    assert 'host_id="timing-h1"' not in "\n".join(
        line for line in body.splitlines() if line.startswith("stability_agent_heartbeat_")
    )


def test_stale_host_snapshot_is_removed(client, db_session, monkeypatch):
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    timing = HeartbeatTiming()
    timing.observe("tick_total", 1.0)
    host = _host(db_session, "timing-stale", timing.snapshot(slow_due=25, disk_due=0))
    assert 'host_id="timing-stale"' in client.get("/metrics").text

    host.last_heartbeat = datetime.now(timezone.utc) - timedelta(days=1)
    db_session.commit()
    body = client.get("/metrics").text
    assert 'host_id="timing-stale"' not in "\n".join(
        line for line in body.splitlines() if line.startswith("stability_agent_heartbeat_")
    )


def test_absent_or_invalid_snapshot_never_looks_like_a_zero_tick(
    client, db_session, monkeypatch,
):
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    _host(db_session, "timing-invalid", {
        "version": 1,
        "phases": {"tick_total": {"buckets": [0], "count": 99, "sum": 0}},
        "slow_due": "bad",
    })
    body = client.get("/metrics").text
    assert 'host_id="timing-invalid"' not in "\n".join(
        line for line in body.splitlines() if line.startswith("stability_agent_heartbeat_")
    )
