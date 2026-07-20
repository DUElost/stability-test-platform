"""ADR-0026 P0 — heartbeat interval backpressure + hardware downsample."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.api.routes.heartbeat import (
    _should_write_hardware_snapshot,
    _suggested_heartbeat_interval,
)


def test_suggested_heartbeat_interval_scales_with_fleet(monkeypatch):
    monkeypatch.setattr("backend.api.routes.heartbeat.HEARTBEAT_INTERVAL_BASE", 20)
    monkeypatch.setattr("backend.api.routes.heartbeat.HEARTBEAT_INTERVAL_MIN", 15)
    monkeypatch.setattr("backend.api.routes.heartbeat.HEARTBEAT_INTERVAL_MAX", 60)

    assert _suggested_heartbeat_interval(0) == 20
    assert _suggested_heartbeat_interval(10) == 21
    assert _suggested_heartbeat_interval(400) == 60  # clamped


def test_hardware_snapshot_downsample_gate(monkeypatch):
    monkeypatch.setattr("backend.api.routes.heartbeat.SNAPSHOT_INTERVAL_SECONDS", 30)
    now = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)
    fresh = SimpleNamespace(hardware_updated_at=None)
    assert _should_write_hardware_snapshot(fresh, now) is True

    recent = SimpleNamespace(hardware_updated_at=now - timedelta(seconds=10))
    assert _should_write_hardware_snapshot(recent, now) is False

    stale = SimpleNamespace(hardware_updated_at=now - timedelta(seconds=31))
    assert _should_write_hardware_snapshot(stale, now) is True


def test_heartbeat_thread_interval_clamp_env(monkeypatch):
    """Agent clamps server hint into STP_HEARTBEAT_INTERVAL_{MIN,MAX}."""
    from backend.agent.heartbeat_thread import HeartbeatThread

    monkeypatch.setenv("STP_HEARTBEAT_INTERVAL_MIN", "10")
    monkeypatch.setenv("STP_HEARTBEAT_INTERVAL_MAX", "120")
    thread = HeartbeatThread(
        api_url="http://test",
        host_id="h1",
        adb_path="adb",
        mount_points=[],
        host_info={},
        poll_interval=20.0,
    )
    assert thread._min_poll_interval == 10.0
    assert thread._max_poll_interval == 120.0

    # Simulate honouring a server hint (same clamp as heartbeat_thread cycle).
    suggested = 45.0
    clamped = max(
        thread._min_poll_interval,
        min(thread._max_poll_interval, suggested),
    )
    thread._poll_interval = clamped
    assert thread._poll_interval == 45.0

    # Over-max hint is clamped.
    over = max(
        thread._min_poll_interval,
        min(thread._max_poll_interval, 999.0),
    )
    assert over == 120.0
