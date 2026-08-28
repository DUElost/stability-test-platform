"""ADR-0019 Phase 3c — Agent 侧 capacity/health 计算模块。

纯函数，无 IO，不依赖外部状态。由 HeartbeatThread._tick 同步调用。
"""

import math
import os
import logging
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CLAIM_SLOTS = 5  # 与 operation_scheduler 的 permit 默认对齐（#483）


def _coerce_usage_percent(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or not 0.0 <= value <= 100.0:
        return None
    return value


def _disk_usage_percent(system_stats: dict) -> Optional[float]:
    blob = system_stats.get("disk_usage")
    if not isinstance(blob, dict):
        return None
    return _coerce_usage_percent(blob.get("usage_percent"))


def compute_capacity(
    active_job_count: int,
    active_device_count: int,
    online_healthy_devices: int,
    total_devices: int,
    system_stats: dict,
    mount_status: dict,
    adb_server_conflict: bool = False,
    max_claim_slots: "Optional[int]" = None,
) -> dict:
    """返回 {"capacity": {...}, "health": {...}}。

    total_devices — 本 host 上报的设备总数（含离线/不健康），用于判断 adb 全死。
    有效槽位 = min(空闲设备数, 主机健康状态, 认领上限)。
    max_concurrent_jobs 已删除——空闲设备数原本是唯一上限；#483 追加
    认领上限（默认 5，与 OperationScheduler permit 对齐）：
    否则同 host 大批次会把全部设备一次认领，worker 池过大 → 密集
    重启/重枚举风暴压垮 hub（.80 19 台并发刷写 15 台写失败的根因）。
    """
    health = _compute_health(
        system_stats,
        mount_status,
        online_healthy_devices,
        total_devices,
        adb_server_conflict=adb_server_conflict,
    )
    health_limit = _compute_health_limit(
        system_stats, mount_status,
        online_healthy_devices, total_devices,
    )

    device_slots = max(0, online_healthy_devices - active_device_count)
    if max_claim_slots is None:
        max_claim_slots = configured_max_claim_slots()
    effective_slots = min(device_slots, health_limit, max_claim_slots)

    capacity = {
        "active_jobs": active_job_count,
        "active_devices": active_device_count,
        "online_healthy_devices": online_healthy_devices,
        "available_slots": device_slots,
        "effective_slots": effective_slots,
    }

    return {"capacity": capacity, "health": health}


def configured_max_claim_slots() -> int:
    """认领上限：STP_MAX_CLAIM_SLOTS（默认 5，与 STP_MAX_CONCURRENT_OPERATIONS
    对齐）。钳在 compute_capacity 层，使 agent 一次认领的 job 数不超过该值——
    permit 只限步骤并发，挡不住「17 个 worker 全活跃」的认领风暴。"""
    raw = os.getenv("STP_MAX_CLAIM_SLOTS", str(_DEFAULT_MAX_CLAIM_SLOTS))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("invalid_max_claim_slots raw=%r default=%d",
                       raw, _DEFAULT_MAX_CLAIM_SLOTS)
        value = _DEFAULT_MAX_CLAIM_SLOTS
    return max(value, 1)


def _compute_health_limit(
    system_stats: dict,
    mount_status: dict,
    online_healthy_devices: int,
    total_devices: int,
) -> int:
    """二元 gate：主机健康时返回大值（不限制），不健康时返回 0。

    与 _compute_health 共享阈值常量，变更阈值时需同步修改两处。
    """
    cpu = system_stats.get("cpu_load", 0)
    ram = system_stats.get("ram_usage", 0)
    disk = _disk_usage_percent(system_stats)
    mount_ok = all(m.get("ok", False) for m in mount_status.values()) if mount_status else True
    adb_all_dead = online_healthy_devices == 0 and total_devices > 0

    if cpu > 90 or ram > 95 or disk is None or disk > 95 or not mount_ok or adb_all_dead:
        return 0
    return 10_000  # effectively unlimited — real limit is free device count


def _compute_health(
    system_stats: dict,
    mount_status: dict,
    online_healthy_devices: int,
    total_devices: int,
    adb_server_conflict: bool = False,
) -> dict:
    """产出结构化 health 快照。

    阈值与 _compute_health_limit 完全一致（blocking reason → UNSCHEDULABLE）；
    warning 级 reason（如 adb_multiple_servers）只进 DEGRADED，不打闸。
    """
    reasons: List[str] = []
    cpu = system_stats.get("cpu_load", 0)
    ram = system_stats.get("ram_usage", 0)
    disk = _disk_usage_percent(system_stats)
    mount_ok = all(m.get("ok", False) for m in mount_status.values()) if mount_status else True
    adb_dead = online_healthy_devices == 0 and total_devices > 0

    if cpu > 90:
        reasons.append("cpu_high")
    if ram > 95:
        reasons.append("ram_high")
    if disk is None:
        reasons.append("disk_unknown")
    elif disk > 95:
        reasons.append("disk_high")
    if not mount_ok:
        reasons.append("mount_failed")
    if adb_dead:
        reasons.append("adb_low_healthy_devices")
    if adb_server_conflict:
        reasons.append("adb_multiple_servers")

    if cpu > 90 or ram > 95 or disk is None or disk > 95 or not mount_ok or adb_dead:
        status = "UNSCHEDULABLE"
    elif reasons:
        status = "DEGRADED"
    else:
        status = "HEALTHY"

    return {
        "status": status,
        "reasons": reasons,
        "cpu_load": cpu,
        "ram_usage": ram,
        "disk_usage": disk,
        "mount_ok": mount_ok,
        "adb_ok": not adb_dead,
    }
