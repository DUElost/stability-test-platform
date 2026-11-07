"""vbus 刷机作业的准入判定（#3892 / ADR-0063 D4）。

关机设备一律是 ``OFFLINE``（心跳超时，``heartbeat.py``），派发准入与 Agent 认领都会
把它挡掉。ADR-0063 D4 只为一种作业放行 ``device_offline``：``lifecycle.init`` 的第一个
步骤是 ``script:flash_firmware`` 且参数 ``trigger_mode == "vbus_cycle"``——这类作业
本来就是要在设备没有 ADB 时用 USB 切电把它拉进 BROM。

派发侧（Plan 快照）与认领侧（作业 ``pipeline_def``）都经 :func:`is_vbus_flash_lifecycle`
判定，避免两处判据漂移。其它拒绝理由（退役、欠账、租约、主机离线等）不受影响。
"""

from __future__ import annotations

from typing import Any

from backend.services.plan_dispatcher_core import (
    PlanDispatchError,
    build_lifecycle_from_snapshot,
)

VBUS_FLASH_ACTION = "script:flash_firmware"
VBUS_TRIGGER_MODE = "vbus_cycle"


def is_vbus_flash_lifecycle(lifecycle: Any) -> bool:
    """``lifecycle.init[0]`` 是否为 ``trigger_mode=vbus_cycle`` 的 flash_firmware 步骤。"""
    if not isinstance(lifecycle, dict):
        return False
    init = lifecycle.get("init")
    if not isinstance(init, list) or not init:
        return False
    first = init[0]
    if not isinstance(first, dict) or first.get("action") != VBUS_FLASH_ACTION:
        return False
    params = first.get("params")
    return isinstance(params, dict) and params.get("trigger_mode") == VBUS_TRIGGER_MODE


def is_vbus_flash_pipeline(pipeline_def: Any) -> bool:
    """作业 ``pipeline_def``（``{"lifecycle": ...}``）是否为 vbus 刷机作业。"""
    if not isinstance(pipeline_def, dict):
        return False
    return is_vbus_flash_lifecycle(pipeline_def.get("lifecycle"))


def is_vbus_flash_snapshot(plan_snapshot: Any) -> bool:
    """PlanRun 快照是否为 vbus 刷机计划；快照不可物化时按否处理（不放行）。"""
    if not isinstance(plan_snapshot, dict):
        return False
    try:
        lifecycle = build_lifecycle_from_snapshot(plan_snapshot)
    except (PlanDispatchError, TypeError, ValueError, AttributeError):
        return False
    return is_vbus_flash_lifecycle(lifecycle)
