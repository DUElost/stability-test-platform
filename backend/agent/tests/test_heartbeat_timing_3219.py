"""#3219: bounded timing buckets cross the Agent heartbeat boundary."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.agent.contracts.heartbeat_timing import BUCKETS_SECONDS
from backend.agent.heartbeat_thread import HeartbeatThread
from backend.agent.heartbeat_timing import HeartbeatTiming


def test_timing_buckets_are_cumulative_and_skip_invalid_values():
    timing = HeartbeatTiming()
    timing.observe("tick_total", 0.01)
    timing.observe("tick_total", 0.2)
    timing.observe("tick_total", float("nan"))
    row = timing.snapshot(slow_due=25, disk_due=8)["phases"]["tick_total"]
    assert row["count"] == 2
    assert row["buckets"][BUCKETS_SECONDS.index(0.01)] == 1
    assert row["buckets"][BUCKETS_SECONDS.index(0.25)] == 2
    assert row["buckets"][-1] == 2
    assert row["sum"] == pytest.approx(0.21)
    assert timing.snapshot(slow_due=25, disk_due=8)["slow_due"] == 25


def test_next_heartbeat_carries_previous_tick_and_actual_start_interval(monkeypatch):
    clock = {"now": 100.0}

    def advance(seconds):
        clock["now"] += seconds

    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.time",
        SimpleNamespace(monotonic=lambda: clock["now"]),
    )
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.discover_devices",
        lambda adb: advance(0.1) or [{"serial": "S1", "adb_state": "device"}],
    )

    def fake_collect(adb, serial, *, raw_adb_state, include_metrics, timing_sink):
        advance(0.2)
        timing_sink("fast", 0.2)
        if include_metrics:
            advance(0.3)
            timing_sink("slow", 0.3)
        return {"adb_state": "device", "adb_connected": True}

    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.collect_device_info",
        fake_collect,
    )
    sent = []

    def fake_send(*args, **kwargs):
        sent.append(kwargs["system_stats"]["heartbeat_timing"])
        advance(0.4)
        return {"ok": True}

    monkeypatch.setattr("backend.agent.heartbeat_thread.send_heartbeat", fake_send)
    thread = HeartbeatThread(
        api_url="http://isolated", host_id="h", adb_path="adb",
        mount_points=[], host_info={}, poll_interval=20,
    )
    thread._maybe_sample_disk = lambda discovered, settings: 0
    thread._read_heartbeat_settings_safe = lambda: SimpleNamespace(
        stp_device_info_sample_interval_seconds=0,
        adb_auto_repair_enabled=False,
        adb_reconnect_auto_enabled=False,
    )
    thread._safe_tick()
    assert sent[0]["phases"] == {}  # HTTP cannot report its own duration yet.
    advance(20)
    thread._safe_tick()
    phases = sent[1]["phases"]
    assert phases["tick_total"]["count"] == 1
    assert phases["discover"]["count"] == 1
    assert phases["probe_fast_max"]["count"] == 1
    assert phases["probe_slow_max"]["count"] == 1
    assert phases["http"]["count"] == 1
    assert sent[1]["slow_due"] == 1
    assert phases["tick_interval"]["count"] == 1
    assert phases["tick_interval"]["sum"] >= 20.9
    assert "S1" not in str(sent[1])
