"""ADR-0019 Phase 3c — Agent 侧 capacity/health 计算模块。

纯函数，无 IO，不依赖外部状态。由 HeartbeatThread._tick 同步调用。
"""

import math
import os
import logging
from typing import Any, List, Optional, Sequence

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
    usb_device_count: Optional[int] = None,
    adb_interface_count: Optional[int] = None,
    adb_state_counts: Optional[dict] = None,
    usb_root_hub_count: Optional[int] = None,
    usb_fault_reasons: Optional[Sequence[str]] = None,
    usb_kernel_log_channel: Optional[str] = None,
) -> dict:
    """返回 {"capacity": {...}, "health": {...}}。

    total_devices — 本 host 上报的设备总数（含离线/不健康），用于判断 adb 全死。
    有效槽位 = min(空闲设备数, 主机健康状态, 认领上限)。
    max_concurrent_jobs 已删除——空闲设备数原本是唯一上限；#483 追加
    认领上限（默认 5，与 OperationScheduler permit 对齐）：
    否则同 host 大批次会把全部设备一次认领，worker 池过大 → 密集
    重启/重枚举风暴压垮 hub（.80 19 台并发刷写 15 台写失败的根因）。

    usb_device_count — lsusb 枚举到的疑似 Android 设备数，**纯观测对照**：
    与 online_healthy_devices（adb devices 口径）并排展示，差值即 ADB 未枚举到的
    物理设备（授权/驱动/多 fork-server 等）。为 None 表示无法判定（非 0）。
    不参与 device_slots / effective_slots；health 仅用于空树（#2902）与
    L4「USB 有设备但 ADB 接口全无」（#3046）两条 warning reason。

    #2902（L2/L3/L4 分辨，均为观测面，不改变槽位计算）：
    - ``adb_interface_count`` — sysfs 里暴露 ADB 接口（ff:42）的设备数。与
      ``adb_state_counts.device`` 一起可判「L2：接口集合 ⊋ adb 列表」；
    - ``adb_state_counts`` — `adb devices` 的 state 分桶（device/offline/
      unauthorized/other），非 device 桶非空即 L3（设备侧 adbd/授权）；
    - ``usb_root_hub_count`` — lsusb 里 root hub 条数，仅作**空树判据输入**
      （不上报：见 capacity dict 注释），与 usb_device_count 合用判「USB 树上只剩
      控制器」→ 空树 reason（覆盖 total_devices==0 的旧门禁短路）。

    usb_fault_reasons — 内核 USB 子系统故障（#2900，`kernel_usb_faults` 判定后传入）：
    warning 级 reason，只进 DEGRADED，不进 health_limit（打闸口径见 #2902）。

    usb_kernel_log_channel — #2957：上面那些 USB reason 的**判据通道**是否可读
    （`kernel_usb_faults.CHANNEL_*`）。放 capacity 不放 health.reasons——它是传感器
    自身状态而不是主机故障：进 reasons 会把整个 fleet 刷成 DEGRADED（页面噪声 +
    与真故障同色），而我们要能单独问出「这条判据今天算不算数」。
    与 `usb_device_count` 同族：纯观测，不参与任何槽位/门禁计算。
    """
    health = _compute_health(
        system_stats,
        mount_status,
        online_healthy_devices,
        total_devices,
        adb_server_conflict=adb_server_conflict,
        usb_device_count=usb_device_count,
        usb_root_hub_count=usb_root_hub_count,
        adb_interface_count=adb_interface_count,
        usb_fault_reasons=usb_fault_reasons,
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
        "usb_device_count": usb_device_count,
        "adb_interface_count": adb_interface_count,
        "adb_state_counts": adb_state_counts,
        # #2957：内核日志通道可用性（ok / unavailable / unknown）。键名取短是为了留在
        # #2902 立的心跳 payload 预算里；值用完整词，它会直接成为 PromQL 的 label 值。
        "usb_kernel_log": usb_kernel_log_channel,
        # usb_root_hub_count **不上报**：它只是 `usb_tree_empty` 判据的输入（空树基线），
        # 页面不需要；上报会让心跳 payload 增幅越过 issue 的 <100B 验收线（实测 121B）。
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
    usb_device_count: Optional[int] = None,
    usb_root_hub_count: Optional[int] = None,
    adb_interface_count: Optional[int] = None,
    usb_fault_reasons: Optional[Sequence[str]] = None,
) -> dict:
    """产出结构化 health 快照。

    阈值与 _compute_health_limit 完全一致（blocking reason → UNSCHEDULABLE）；
    warning 级 reason（如 adb_multiple_servers、usb_tree_empty、
    adb_interfaces_missing）只进 DEGRADED，不打闸——观测面，且当下本就没有
    可调度的健康设备。

    ``usb_fault_reasons`` 由 `kernel_usb_faults`（#2900）判定后传入——内核 USB 子系统
    故障（xHCI 主控死亡 / 慢性链路劣化）**独立于设备数**，故不参与
    `_compute_health_limit` 的打闸判据（那是 #2902 的门禁议题），只把 host 从
    HEALTHY 拉成 DEGRADED，让「心跳正常但 USB 全瞎」不再无声。
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
    if _usb_tree_empty(usb_device_count, usb_root_hub_count, total_devices):
        reasons.append("usb_tree_empty")
    if _adb_interfaces_missing(usb_device_count, adb_interface_count):
        reasons.append("adb_interfaces_missing")
    for reason in usb_fault_reasons or ():
        if reason not in reasons:
            reasons.append(reason)

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


def _usb_tree_empty(
    usb_device_count: Optional[int],
    usb_root_hub_count: Optional[int],
    total_devices: int,
) -> bool:
    """USB 树上只剩控制器、且没有任何已发现设备（#2902）。

    旧门禁的盲区：`adb_low_healthy_devices` 要求 ``total_devices > 0``——整树死亡
    （xHCI 失联/被解绑）时 `total_devices == 0`，host 恒显 HEALTHY，**最严重的
    故障形态恰好是唯一不告警的形态**（.63 与 8.87 实测）。本判据补上这一格：

    - 两个计数都**可判定**（None = 采集失败，不据此报警）；
    - ``usb_device_count <= usb_root_hub_count``（lsusb 里只有 1d6b 系控制器）；
    - 且 ``total_devices == 0``（agent 一台设备都没发现）。

    warning 级（DEGRADED）：空树时没有设备可调度，无需打闸，但必须在页面上可见。
    """
    if usb_device_count is None or usb_root_hub_count is None:
        return False
    return usb_device_count <= usb_root_hub_count and total_devices == 0


def _adb_interfaces_missing(
    usb_device_count: Optional[int],
    adb_interface_count: Optional[int],
) -> bool:
    """L4：USB 枚举到设备，但 sysfs 上 ADB 接口（ff:42）数为 0（#3046）。

    ``adb_low_healthy_devices`` 看的是 adb devices 列表（``total_devices``），
    ``usb_tree_empty`` 要求 USB 计数≈0——二者之间的缝：USB n>0 且接口全无时，
    host 会静默 HEALTHY（.65/.20 实测数周）。本判据只补观测面，不打闸。

    None = 采集失败，不据此报警（未知 ≠ 缺失）。
    """
    if usb_device_count is None or adb_interface_count is None:
        return False
    return usb_device_count > 0 and adb_interface_count == 0
