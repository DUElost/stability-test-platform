"""ADR-0019 Phase 3c CapacityReporter tests.

max_concurrent_jobs removed — capacity is now gated by free device count and health only.
"""

import json

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


# ── usb_device_count：lsusb 对照值（纯观测，不得影响调度） ────────────────────

def test_usb_device_count_is_reported_in_capacity():
    result = compute_capacity(
        active_job_count=1,
        active_device_count=0,
        online_healthy_devices=3,
        total_devices=3,
        system_stats=_healthy_system_stats(),
        mount_status=_healthy_mount_status(),
        usb_device_count=5,
    )
    assert result["capacity"]["usb_device_count"] == 5


def test_usb_device_count_defaults_to_none():
    """未传（旧 Agent / 采集失败）→ None，前端显示「—」而非 0。"""
    result = compute_capacity(
        active_job_count=1,
        active_device_count=0,
        online_healthy_devices=3,
        total_devices=3,
        system_stats=_healthy_system_stats(),
        mount_status=_healthy_mount_status(),
    )
    assert result["capacity"]["usb_device_count"] is None


def test_usb_device_count_does_not_affect_scheduling():
    """回归保护：USB 对照值再悬殊，也不得改变槽位或健康判定。

    USB 数 > ADB 数（授权/驱动问题）与 USB 数 < ADB 数是现场常见故障形态；
    两者都必须与不传该字段时逐字段一致，否则会误伤派发。
    """
    baseline = compute_capacity(
        active_job_count=2,
        active_device_count=1,
        online_healthy_devices=8,
        total_devices=10,
        system_stats=_healthy_system_stats(),
        mount_status=_healthy_mount_status(),
    )
    for usb in (0, 1, 8, 99):
        with_usb = compute_capacity(
            active_job_count=2,
            active_device_count=1,
            online_healthy_devices=8,
            total_devices=10,
            system_stats=_healthy_system_stats(),
            mount_status=_healthy_mount_status(),
            usb_device_count=usb,
        )
        assert with_usb["health"] == baseline["health"]
        for key, value in baseline["capacity"].items():
            if key == "usb_device_count":
                continue  # 对照值本身，不参与一致性比较
            assert with_usb["capacity"][key] == value, f"{key} drifted with usb={usb}"


def test_usb_device_count_does_not_rescue_adb_dead_host():
    """USB 能看到设备也不能让 adb 全死的主机恢复调度（门禁只看 adb 口径）。"""
    result = compute_capacity(
        active_job_count=0,
        active_device_count=0,
        online_healthy_devices=0,
        total_devices=5,
        system_stats=_healthy_system_stats(),
        mount_status=_healthy_mount_status(),
        usb_device_count=5,
    )
    assert result["capacity"]["effective_slots"] == 0
    assert result["health"]["status"] == "UNSCHEDULABLE"
    assert "adb_low_healthy_devices" in result["health"]["reasons"]


# ── #2902：L2/L3/L4 分辨信号 + total==0 的空树门禁 ───────────────────────────

def _cap(**overrides):
    """构造调用参数（默认：健康主机、零设备），按需覆盖。"""
    kwargs = dict(
        active_job_count=0,
        active_device_count=0,
        online_healthy_devices=0,
        total_devices=0,
        system_stats=_healthy_system_stats(),
        mount_status=_healthy_mount_status(),
    )
    kwargs.update(overrides)
    return compute_capacity(**kwargs)


def test_usb_tree_empty_degrades_healthy_looking_host():
    """整树死亡形态：lsusb 只剩 root hub、agent 一台设备都没发现。

    旧门禁在这一形态下恒显 HEALTHY（`adb_low_healthy_devices` 要求 total>0）——
    最严重的故障形态恰好是唯一不告警的形态（#2902 的核心缺口）。
    """
    result = _cap(usb_device_count=2, usb_root_hub_count=2)

    assert result["health"]["status"] == "DEGRADED"
    assert "usb_tree_empty" in result["health"]["reasons"]


def test_usb_tree_empty_not_fired_when_devices_discovered():
    result = _cap(total_devices=16, online_healthy_devices=16,
                  usb_device_count=2, usb_root_hub_count=2)
    assert "usb_tree_empty" not in result["health"]["reasons"]


def test_usb_tree_empty_not_fired_when_usb_has_peripherals():
    """USB 上还有外设（设备数 > root hub 数）→ 不是空树。"""
    result = _cap(usb_device_count=16, usb_root_hub_count=2)
    assert "usb_tree_empty" not in result["health"]["reasons"]


def test_usb_tree_empty_not_fired_when_probe_failed():
    """采集失败（None）不得据以报警——未知 ≠ 空。"""
    assert "usb_tree_empty" not in _cap(
        usb_device_count=None, usb_root_hub_count=None
    )["health"]["reasons"]
    assert "usb_tree_empty" not in _cap(
        usb_device_count=2, usb_root_hub_count=None
    )["health"]["reasons"]


def test_usb_tree_empty_is_warning_level_not_blocking():
    """warning 级：空树不进 UNSCHEDULABLE（此时本就没有设备可调度）。"""
    health = _cap(usb_device_count=2, usb_root_hub_count=2)["health"]
    assert health["status"] == "DEGRADED"
    assert health["adb_ok"] is True


def test_adb_interfaces_missing_l4_degrades_silent_healthy_host():
    """#3046 L4：USB n>0 且 ADB 接口全无 → DEGRADED（.65/.20 静默 HEALTHY 的缝）。

    adb devices 列表为空时 total_devices==0，adb_low_healthy_devices 不成立；
    USB 有外设时 usb_tree_empty 也不成立——必须单独合取。
    """
    result = _cap(
        total_devices=0,
        online_healthy_devices=0,
        usb_device_count=18,
        usb_root_hub_count=2,
        adb_interface_count=0,
    )
    assert result["health"]["status"] == "DEGRADED"
    assert "adb_interfaces_missing" in result["health"]["reasons"]
    assert "usb_tree_empty" not in result["health"]["reasons"]
    assert "adb_low_healthy_devices" not in result["health"]["reasons"]


def test_adb_interfaces_missing_not_fired_when_interfaces_present():
    result = _cap(
        usb_device_count=16, usb_root_hub_count=2, adb_interface_count=12,
        total_devices=12, online_healthy_devices=12,
    )
    assert "adb_interfaces_missing" not in result["health"]["reasons"]


def test_adb_interfaces_missing_not_fired_when_probe_unknown():
    assert "adb_interfaces_missing" not in _cap(
        usb_device_count=18, adb_interface_count=None,
    )["health"]["reasons"]
    assert "adb_interfaces_missing" not in _cap(
        usb_device_count=None, adb_interface_count=0,
    )["health"]["reasons"]


def test_adb_interfaces_missing_is_warning_not_blocking():
    health = _cap(
        usb_device_count=16, usb_root_hub_count=2, adb_interface_count=0,
    )["health"]
    assert health["status"] == "DEGRADED"
    assert health["adb_ok"] is True


def test_l2_l3_l4_signals_reported_in_capacity():
    state_counts = {"device": 1, "offline": 1, "unauthorized": 1, "other": 0}
    cap = _cap(
        total_devices=3, online_healthy_devices=1,
        usb_device_count=3, usb_root_hub_count=2,
        adb_interface_count=3, adb_state_counts=state_counts,
    )["capacity"]

    assert cap["adb_interface_count"] == 3
    assert cap["adb_state_counts"] == state_counts
    # root hub 数只作空树判据输入，不上报（心跳体积预算内）：
    assert "usb_root_hub_count" not in cap


def test_l2_l3_l4_signals_default_to_none_without_wiring():
    """未接线时行为与引入前一致（三键为 None、health 不新增 reason）。"""
    result = _cap(total_devices=2, online_healthy_devices=2)
    cap = result["capacity"]

    assert cap["adb_interface_count"] is None
    assert cap["adb_state_counts"] is None
    assert "usb_tree_empty" not in result["health"]["reasons"]


def test_usb_kernel_log_channel_is_observation_only():
    """#2957：通道态只进 capacity——进 reasons 会把整 fleet 刷成 DEGRADED。

    「传感器读不到」不是「主机坏了」：把它塞进 health.reasons 会有两个后果，
    都比它的收益大——① status 变 DEGRADED，页面上一片红却没有任何一台设备真的
    出问题；②运维学会忽略这条红，于是真 reason（#2900 的 xHCI 死亡）也被一起淹掉。
    """
    result = _cap(usb_kernel_log_channel="unavailable")

    assert result["capacity"]["usb_kernel_log"] == "unavailable"
    assert result["health"]["status"] == "HEALTHY"
    assert result["health"]["reasons"] == []


def test_usb_kernel_log_channel_defaults_to_none_when_unwired():
    """未接线时键存在但为 None：控制面按 unknown 处理，不猜「正常」。"""
    assert _cap()["capacity"]["usb_kernel_log"] is None


def test_usb_kernel_log_channel_does_not_change_slots():
    """通道态不得参与任何槽位/打闸计算（与 usb_device_count 同族的纯观测口径）。"""
    with_dark = _cap(
        active_job_count=0, active_device_count=0,
        online_healthy_devices=4, total_devices=4,
        system_stats=_healthy_system_stats(), mount_status=_healthy_mount_status(),
        usb_kernel_log_channel="unavailable",
    )
    baseline = _cap(
        active_job_count=0, active_device_count=0,
        online_healthy_devices=4, total_devices=4,
        system_stats=_healthy_system_stats(), mount_status=_healthy_mount_status(),
    )
    assert with_dark["capacity"]["effective_slots"] == baseline["capacity"]["effective_slots"]
    assert with_dark["capacity"]["available_slots"] == baseline["capacity"]["available_slots"]


def test_new_payload_keys_within_heartbeat_budget():
    """心跳 payload 增幅 <100B（#2902 验收项）。"""
    cap = _cap(
        adb_interface_count=16,
        adb_state_counts={"device": 14, "offline": 1, "unauthorized": 1, "other": 0},
    )["capacity"]
    cap = _cap(
        adb_interface_count=16,
        adb_state_counts={"device": 14, "offline": 1, "unauthorized": 1, "other": 0},
    )["capacity"]
    new_keys = {k: cap[k] for k in ("adb_interface_count", "adb_state_counts")}
    assert len(json.dumps(new_keys, separators=(",", ":"))) < 100


def test_usb_kernel_log_key_has_its_own_payload_budget():
    """#2957 的增量单独计预算，不并进 #2902 那条断言。

    合并成一条「三键合计 <100B」会让两个 issue 的验收项互相绑架：任一侧改名都把对方
    打红，而真正该被约束的是**每个 issue 各自引入的增量**。实测本键最坏取值
    （"unavailable"）= 31 字节，约为 #2902 两键合计的三分之一。
    """
    cap = _cap(usb_kernel_log_channel="unavailable")["capacity"]
    assert len(json.dumps({"usb_kernel_log": cap["usb_kernel_log"]}, separators=(",", ":"))) < 60


# ── #3092: 单实例守卫降级 ────────────────────────────────────────────────────

def test_single_instance_guard_degraded_is_reported():
    """守卫降级（锁文件连只读都打不开）→ health.reasons 出现专属 reason。"""
    health = _cap(single_instance_degraded=True)["health"]

    assert "single_instance_guard_degraded" in health["reasons"]
    assert health["status"] == "DEGRADED"


def test_single_instance_guard_degraded_does_not_block_scheduling():
    """降级是观测事实，不打闸——health_limit / 槽位口径与 healthy 主机一致。"""
    degraded = _cap(
        online_healthy_devices=4, total_devices=4, single_instance_degraded=True
    )
    baseline = _cap(online_healthy_devices=4, total_devices=4)

    assert degraded["capacity"]["effective_slots"] == baseline["capacity"]["effective_slots"]
    assert degraded["capacity"]["available_slots"] == baseline["capacity"]["available_slots"]


def test_single_instance_guard_reason_absent_by_default():
    """未降级的主机不得带上这条 reason（否则全 fleet 刷成 DEGRADED）。"""
    assert "single_instance_guard_degraded" not in _cap()["health"]["reasons"]
