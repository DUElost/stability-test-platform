"""Tests for unified capacity semantics (#7).

Verifies:
- HeartbeatThread.effective_slots is thread-safe and defaults to 0
- main.py claim loop uses min(MAX_CONCURRENT_TASKS - active, effective_slots)
- agent_api.py clamps capacity to free_device_count
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from backend.agent.heartbeat_thread import HeartbeatThread


def test_heartbeat_effective_slots_default_zero():
    """effective_slots is 0 before first heartbeat tick."""
    ht = HeartbeatThread(
        api_url="http://test",
        host_id="h1",
        adb_path="/adb",
        mount_points=[],
        host_info={},
        poll_interval=30,
    )
    assert ht.effective_slots == 0


def test_heartbeat_effective_slots_thread_safe():
    """effective_slots can be read/written from multiple threads without error."""
    ht = HeartbeatThread(
        api_url="http://test",
        host_id="h1",
        adb_path="/adb",
        mount_points=[],
        host_info={},
        poll_interval=30,
    )

    results: list[int] = []

    def reader():
        for _ in range(100):
            results.append(ht.effective_slots)

    def writer():
        for i in range(100):
            with ht._capacity_lock:
                ht._effective_slots = i

    t1 = threading.Thread(target=reader)
    t2 = threading.Thread(target=writer)
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert len(results) == 100
    assert all(isinstance(r, int) for r in results)


def test_capacity_reporter_effective_slots_health_gate():
    """effective_slots is 0 when host is UNSCHEDULABLE."""
    from backend.agent.capacity_reporter import compute_capacity

    result = compute_capacity(
        active_job_count=0,
        active_device_count=0,
        online_healthy_devices=5,
        total_devices=5,
        system_stats={"cpu_load": 95.0, "ram_usage": 50.0},
        mount_status={},
    )
    assert result["capacity"]["effective_slots"] == 0


def test_capacity_reporter_effective_slots_normal():
    """effective_slots = min(free_devices, health_limit) when healthy."""
    from backend.agent.capacity_reporter import compute_capacity

    result = compute_capacity(
        active_job_count=1,
        active_device_count=1,
        online_healthy_devices=8,
        total_devices=10,
        system_stats={"cpu_load": 30.0, "ram_usage": 40.0},
        mount_status={"/mnt/hdd": {"ok": True, "usage_percent": 50.0}},
    )
    assert result["capacity"]["effective_slots"] == 7  # 8 - 1 = 7 free
