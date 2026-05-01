"""ADR-0019 Phase 3c — Agent 侧 capacity/health 计算模块。

纯函数，无 IO，不依赖外部状态。由 HeartbeatThread._tick 同步调用。
"""

from typing import List


def compute_capacity(
    max_concurrent_jobs: int,
    active_job_count: int,
    active_device_count: int,
    online_healthy_devices: int,
    total_devices: int,
    system_stats: dict,
    mount_status: dict,
) -> dict:
    """返回 {"capacity": {...}, "health": {...}}。

    total_devices — 本 host 上报的设备总数（含离线/不健康），用于判断 adb 全死。
    """
    health = _compute_health(system_stats, mount_status, online_healthy_devices, total_devices)
    health_limit = _compute_health_limit(
        system_stats, mount_status,
        online_healthy_devices, total_devices, max_concurrent_jobs,
    )

    job_slots = max(0, max_concurrent_jobs - active_job_count)
    device_slots = max(0, online_healthy_devices - active_device_count)
    effective_slots = min(job_slots, device_slots, health_limit)

    capacity = {
        "max_concurrent_jobs": max_concurrent_jobs,
        "active_jobs": active_job_count,
        "active_devices": active_device_count,
        "online_healthy_devices": online_healthy_devices,
        "available_slots": job_slots,
        "effective_slots": effective_slots,
    }

    return {"capacity": capacity, "health": health}


def _compute_health_limit(
    system_stats: dict,
    mount_status: dict,
    online_healthy_devices: int,
    total_devices: int,
    max_concurrent_jobs: int,
) -> int:
    """二元 gate：返回 max_concurrent_jobs 或 0。

    与 _compute_health 共享阈值常量，变更阈值时需同步修改两处。
    """
    cpu = system_stats.get("cpu_load", 0)
    ram = system_stats.get("ram_usage", 0)
    disk = system_stats.get("disk_usage", {}).get("usage_percent", 0)
    mount_ok = all(m.get("ok", False) for m in mount_status.values()) if mount_status else True
    adb_all_dead = online_healthy_devices == 0 and total_devices > 0

    if cpu > 90 or ram > 95 or disk > 95 or not mount_ok or adb_all_dead:
        return 0
    return max_concurrent_jobs


def _compute_health(
    system_stats: dict,
    mount_status: dict,
    online_healthy_devices: int,
    total_devices: int,
) -> dict:
    """产出结构化 health 快照。

    阈值与 _compute_health_limit 完全一致。Phase 3c 只产出 HEALTHY / UNSCHEDULABLE。
    DEGRADED 是前端预留状态，留给后续增加 warning 阈值（如 CPU>80）。
    """
    reasons: List[str] = []
    cpu = system_stats.get("cpu_load", 0)
    ram = system_stats.get("ram_usage", 0)
    disk = system_stats.get("disk_usage", {}).get("usage_percent", 0)
    mount_ok = all(m.get("ok", False) for m in mount_status.values()) if mount_status else True
    adb_dead = online_healthy_devices == 0 and total_devices > 0

    if cpu > 90:
        reasons.append("cpu_high")
    if ram > 95:
        reasons.append("ram_high")
    if disk > 95:
        reasons.append("disk_high")
    if not mount_ok:
        reasons.append("mount_failed")
    if adb_dead:
        reasons.append("adb_low_healthy_devices")

    if cpu > 90 or ram > 95 or disk > 95 or not mount_ok or adb_dead:
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
