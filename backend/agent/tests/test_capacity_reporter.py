"""ADR-0019 Phase 3c CapacityReporter tests.

max_concurrent_jobs removed — capacity is now gated by free device count and health only.
"""

import pytest

from backend.agent.capacity_reporter import compute_capacity


# ── Helpers ──────────────────────────────────────────────────────────────────

def _healthy_system_stats():
    return {"cpu_load": 20, "ram_usage": 50, "disk_usage": {"usage_percent": 40}}


def _healthy_mount_status():
    return {"/mnt/data": {"ok": True}}


# ── Test 1: healthy, full slots ──────────────────────────────────────────────

def test_all_healthy_full_slots():
    result = compute_capacity(
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
    assert cap["available_slots"] == 7   # 8 - 1 = 7 free device slots
    # #483: 认领上限默认 5（与 permit 对齐）——available 仍报真实空闲数，
    # effective 被钳到 5
    assert cap["effective_slots"] == 5


# ── Test 2: CPU high → UNSCHEDULABLE ─────────────────────────────────────────

def test_cpu_high_unschedulable():
    stats = {"cpu_load": 95, "ram_usage": 50, "disk_usage": {"usage_percent": 40}}
    result = compute_capacity(
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


# ── Test 3: RAM high → UNSCHEDULABLE ─────────────────────────────────────────

def test_ram_high_unschedulable():
    stats = {"cpu_load": 20, "ram_usage": 97, "disk_usage": {"usage_percent": 40}}
    result = compute_capacity(
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


# ── Test 4: disk high → UNSCHEDULABLE ────────────────────────────────────────

@pytest.mark.parametrize(
    "stats",
    [
        {"cpu_load": 20, "ram_usage": 50, "disk_usage": {"usage_percent": None}},
        {"cpu_load": 20, "ram_usage": 50, "disk_usage": {}},
        {"cpu_load": 20, "ram_usage": 50},
        {"cpu_load": 20, "ram_usage": 50, "disk_usage": "n/a"},
        {"cpu_load": 20, "ram_usage": 50, "disk_usage": {"usage_percent": float("nan")}},
        {"cpu_load": 20, "ram_usage": 50, "disk_usage": {"usage_percent": float("inf")}},
        {"cpu_load": 20, "ram_usage": 50, "disk_usage": {"usage_percent": -1}},
        {"cpu_load": 20, "ram_usage": 50, "disk_usage": {"usage_percent": 101}},
    ],
)
def test_disk_unknown_unschedulable(stats):
    result = compute_capacity(
        active_job_count=0,
        active_device_count=0,
        online_healthy_devices=5,
        total_devices=5,
        system_stats=stats,
        mount_status=_healthy_mount_status(),
    )
    assert result["health"]["status"] == "UNSCHEDULABLE"
    assert "disk_unknown" in result["health"]["reasons"]
    assert result["health"]["disk_usage"] is None
    assert result["capacity"]["effective_slots"] == 0


def test_disk_high_unschedulable():
    stats = {"cpu_load": 20, "ram_usage": 50, "disk_usage": {"usage_percent": 97}}
    result = compute_capacity(
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


# ── Test 5: mount failed → UNSCHEDULABLE ─────────────────────────────────────

def test_mount_failed_unschedulable():
    mount = {"/mnt/data": {"ok": False}}
    result = compute_capacity(
        active_job_count=0,
        active_device_count=0,
        online_healthy_devices=5,
        total_devices=5,
        system_stats=_healthy_system_stats(),
        mount_status=mount,
    )
    assert result["health"]["status"] == "UNSCHEDULABLE"
    assert "mount_failed" in result["health"]["reasons"]


# ── Test 6: no healthy devices → UNSCHEDULABLE ───────────────────────────────

def test_device_limit_reduces_slots():
    """online_healthy_devices=0 but total_devices=5 → adb all dead triggers health gate=0."""
    result = compute_capacity(
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


# ── #160: 多 ADB server 冲突 → DEGRADED 但不打闸 ──────────────────────────────

def test_adb_server_conflict_degrades_without_blocking():
    result = compute_capacity(
        active_job_count=1,
        active_device_count=1,
        online_healthy_devices=8,
        total_devices=10,
        system_stats=_healthy_system_stats(),
        mount_status=_healthy_mount_status(),
        adb_server_conflict=True,
    )
    health = result["health"]
    assert health["status"] == "DEGRADED"
    assert "adb_multiple_servers" in health["reasons"]
    # warning 级 reason 不打闸：可见设备仍可调度；#483 认领上限钳 5
    assert result["capacity"]["effective_slots"] == 5


def test_adb_server_conflict_with_blocking_reason_still_unschedulable():
    stats = {"cpu_load": 95, "ram_usage": 50, "disk_usage": {"usage_percent": 40}}
    result = compute_capacity(
        active_job_count=0,
        active_device_count=0,
        online_healthy_devices=5,
        total_devices=5,
        system_stats=stats,
        mount_status=_healthy_mount_status(),
        adb_server_conflict=True,
    )
    assert result["health"]["status"] == "UNSCHEDULABLE"
    assert "adb_multiple_servers" in result["health"]["reasons"]
    assert "cpu_high" in result["health"]["reasons"]
    assert result["capacity"]["effective_slots"] == 0


# ── #483: 认领上限钳制 ─────────────────────────────────────────────────────

def test_max_claim_slots_caps_effective(monkeypatch):
    """默认上限 5：空闲 7 台也最多认领 5。"""
    monkeypatch.delenv("STP_MAX_CLAIM_SLOTS", raising=False)
    result = compute_capacity(
        active_job_count=2,
        active_device_count=1,
        online_healthy_devices=8,
        total_devices=10,
        system_stats=_healthy_system_stats(),
        mount_status=_healthy_mount_status(),
    )
    assert result["capacity"]["available_slots"] == 7
    assert result["capacity"]["effective_slots"] == 5


def test_max_claim_slots_env_override(monkeypatch):
    """显式调大覆盖默认：8 时回到空闲设备数。"""
    monkeypatch.setenv("STP_MAX_CLAIM_SLOTS", "8")
    result = compute_capacity(
        active_job_count=2,
        active_device_count=1,
        online_healthy_devices=8,
        total_devices=10,
        system_stats=_healthy_system_stats(),
        mount_status=_healthy_mount_status(),
    )
    assert result["capacity"]["effective_slots"] == 7


def test_max_claim_slots_floor_one(monkeypatch):
    """钳制至少为 1（坏值/0 都按 1 处理）。"""
    monkeypatch.setenv("STP_MAX_CLAIM_SLOTS", "0")
    result = compute_capacity(
        active_job_count=0,
        active_device_count=0,
        online_healthy_devices=3,
        total_devices=3,
        system_stats=_healthy_system_stats(),
        mount_status=_healthy_mount_status(),
    )
    assert result["capacity"]["effective_slots"] == 1


def test_unhealthy_still_zero_despite_cap(monkeypatch):
    """不健康 host 的 health_limit=0 仍优先于认领上限。"""
    monkeypatch.delenv("STP_MAX_CLAIM_SLOTS", raising=False)
    stats = {"cpu_load": 95, "ram_usage": 50, "disk_usage": {"usage_percent": 40}}
    result = compute_capacity(
        active_job_count=0,
        active_device_count=0,
        online_healthy_devices=5,
        total_devices=5,
        system_stats=stats,
        mount_status=_healthy_mount_status(),
    )
    assert result["capacity"]["effective_slots"] == 0
