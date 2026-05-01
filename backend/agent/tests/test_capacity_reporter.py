"""ADR-0019 Phase 3c CapacityReporter 单元测试。

6 个测试，覆盖 HEALTHY/UNSCHEDULABLE 状态判定和 effective_slots 计算。
"""

import pytest

from backend.agent.capacity_reporter import compute_capacity


# ── Helpers ──────────────────────────────────────────────────────────────────

def _healthy_system_stats():
    return {"cpu_load": 20, "ram_usage": 50, "disk_usage": {"usage_percent": 40}}


def _healthy_mount_status():
    return {"/mnt/data": {"ok": True}}


# ── Test 1: 全部正常 → HEALTHY, 满槽位 ─────────────────────────────────────────

def test_all_healthy_full_slots():
    result = compute_capacity(
        max_concurrent_jobs=10,
        active_job_count=2,
        active_device_count=1,
        online_healthy_devices=8,
        total_devices=10,
        system_stats=_healthy_system_stats(),
        mount_status=_healthy_mount_status(),
    )
    cap = result["capacity"]
    health = result["health"]

    assert health["status"] == "HEALTHY"
    assert health["reasons"] == []
    assert cap["available_slots"] == 8   # 10 - 2
    assert cap["effective_slots"] == 7   # min(8, 8-1=7, 10) = 7


# ── Test 2: CPU 超高 → UNSCHEDULABLE ──────────────────────────────────────────

def test_cpu_high_unschedulable():
    stats = {"cpu_load": 95, "ram_usage": 50, "disk_usage": {"usage_percent": 40}}
    result = compute_capacity(
        max_concurrent_jobs=10,
        active_job_count=0,
        active_device_count=0,
        online_healthy_devices=5,
        total_devices=5,
        system_stats=stats,
        mount_status=_healthy_mount_status(),
    )
    assert result["health"]["status"] == "UNSCHEDULABLE"
    assert "cpu_high" in result["health"]["reasons"]
    assert result["capacity"]["effective_slots"] == 0


# ── Test 3: RAM 超高 → UNSCHEDULABLE ──────────────────────────────────────────

def test_ram_high_unschedulable():
    stats = {"cpu_load": 20, "ram_usage": 97, "disk_usage": {"usage_percent": 40}}
    result = compute_capacity(
        max_concurrent_jobs=10,
        active_job_count=0,
        active_device_count=0,
        online_healthy_devices=5,
        total_devices=5,
        system_stats=stats,
        mount_status=_healthy_mount_status(),
    )
    assert result["health"]["status"] == "UNSCHEDULABLE"
    assert "ram_high" in result["health"]["reasons"]
    assert result["capacity"]["effective_slots"] == 0


# ── Test 4: 磁盘超高 → UNSCHEDULABLE ──────────────────────────────────────────

def test_disk_high_unschedulable():
    stats = {"cpu_load": 20, "ram_usage": 50, "disk_usage": {"usage_percent": 97}}
    result = compute_capacity(
        max_concurrent_jobs=10,
        active_job_count=0,
        active_device_count=0,
        online_healthy_devices=5,
        total_devices=5,
        system_stats=stats,
        mount_status=_healthy_mount_status(),
    )
    assert result["health"]["status"] == "UNSCHEDULABLE"
    assert "disk_high" in result["health"]["reasons"]
    assert result["capacity"]["effective_slots"] == 0


# ── Test 5: 挂载失败 → UNSCHEDULABLE ──────────────────────────────────────────

def test_mount_failed_unschedulable():
    mount = {"/mnt/data": {"ok": False}}
    result = compute_capacity(
        max_concurrent_jobs=10,
        active_job_count=0,
        active_device_count=0,
        online_healthy_devices=5,
        total_devices=5,
        system_stats=_healthy_system_stats(),
        mount_status=mount,
    )
    assert result["health"]["status"] == "UNSCHEDULABLE"
    assert "mount_failed" in result["health"]["reasons"]


# ── Test 6: 无健康设备 → device 约束 effective_slots ──────────────────────────

def test_device_limit_reduces_slots():
    """online_healthy_devices=0 但有 total_devices=5 → adb 全死触发 health gate=0。"""
    result = compute_capacity(
        max_concurrent_jobs=10,
        active_job_count=0,
        active_device_count=0,
        online_healthy_devices=0,
        total_devices=5,
        system_stats=_healthy_system_stats(),
        mount_status=_healthy_mount_status(),
    )
    assert result["health"]["status"] == "UNSCHEDULABLE"
    assert "adb_low_healthy_devices" in result["health"]["reasons"]
    assert result["capacity"]["effective_slots"] == 0
