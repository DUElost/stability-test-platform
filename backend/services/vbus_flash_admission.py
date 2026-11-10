"""vbus 刷机作业的准入判定（#3892 / ADR-0063 D4）。

关机设备一律是 ``OFFLINE``（心跳超时，``heartbeat.py``），派发准入与 Agent 认领都会
把它挡掉。ADR-0063 D4 只为一种作业放行 ``device_offline``：``lifecycle.init`` 的第一个
步骤是 ``script:flash_firmware`` 且参数 ``trigger_mode == "vbus_cycle"``——这类作业
本来就是要在设备没有 ADB 时用 USB 切电把它拉进 BROM。

派发侧（Plan 快照）与认领侧（作业 ``pipeline_def``）都经 :func:`is_vbus_flash_lifecycle`
判定，避免两处判据漂移。判据本体在 ``backend/agent/contracts/vbus_flash.py``，
Agent host 的 init 残留检查推迟（D5 第 3 条）也用它。
其它拒绝理由（退役、欠账、租约、主机离线等）不受影响。
"""

from __future__ import annotations

from typing import Any

from backend.agent.contracts.vbus_flash import is_vbus_flash_lifecycle
from backend.services.plan_dispatcher_core import (
    PlanDispatchError,
    build_lifecycle_from_snapshot,
)

__all__ = [
    "is_vbus_flash_lifecycle",
    "is_vbus_flash_pipeline",
    "is_vbus_flash_plan_steps",
    "is_vbus_flash_snapshot",
]


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


def is_vbus_flash_plan_steps(steps: Any) -> bool:
    """当前 Plan 步骤是否构成 vbus 刷机作业（#3918 / ADR-0063 D7 第 5 条，派发页提示用）。

    按派发同口径取已启用的 init 步骤（``(stage, sort_order)`` 排序），再交给同一判定函数。
    只读步骤自身的 ``params``：``trigger_mode`` 由 Plan 显式选择（D6 第 2 条），不来自脚本缺省。
    派发准入仍以冻结快照为准，本函数不参与准入。
    """
    init = []
    for step in sorted(steps or [], key=lambda s: (getattr(s, "stage", ""), getattr(s, "sort_order", 0))):
        if getattr(step, "enabled", True) is False or getattr(step, "stage", None) != "init":
            continue
        init.append({"action": f"script:{getattr(step, 'script_name', '')}",
                     "params": getattr(step, "params", None)})
    return is_vbus_flash_lifecycle({"init": init})
