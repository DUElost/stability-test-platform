"""设备改绑（host_id 人工改写）业务逻辑（#3892 / ADR-0063 D7 配套）。

背景：`device.host_id` 的唯一自动写路径是心跳（`heartbeat.py`，设备须经 ADB
上报 online）。vbus 断电刷机的设备在刷机前是 powered_off（不上报 ADB），无法
靠心跳改绑；而派发准入 `device_host_drift` 与 prepare 分组都按当前 `host_id`
判定——「先改绑再刷」因此卡死。本服务提供 admin 入口，把设备账面归属改到目标
主机，补齐这条人工路径。

语义（与退役家族同惯例）：

- 行锁 device（`FOR UPDATE`）+ 锁内复检，闭合「复检→写入」窗口；
- 前置：设备**在役**、目标主机**存在且在役**、设备**无活跃 Job / 无 ACTIVE 租约**
  （不顺带中止在跑测试，也不把租约从一台主机抽走）；
- 只改 `host_id`，**不改写 `status`**（心跳所有）、不删历史行；
- 幂等：目标即当前归属时原样返回、不重复审计；
- 事件真源是 `audit_logs`——审计 fail-closed（`record_audit(strict=True)`）。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.device_lease import DeviceLease
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.services.audit_writer import record_audit
from backend.services.errors import Conflict, NotFound
from backend.services.host_upgrade_gate import ACTIVE_JOB_STATUSES

logger = logging.getLogger(__name__)

_ACTIVE_LEASE = "ACTIVE"


def _locked_device(db: Session, device_id: int) -> Device:
    device = (
        db.execute(select(Device).where(Device.id == device_id).with_for_update())
    ).scalars().first()
    if device is None:
        raise NotFound({"code": "DEVICE_NOT_FOUND", "message": "设备不存在"})
    return device


def _assert_idle(db: Session, device_id: int) -> None:
    """改绑前置：活跃 Job 或 ACTIVE 租约即 409（设备必须空闲）。"""
    active_jobs = (
        db.query(JobInstance)
        .filter(
            JobInstance.device_id == device_id,
            JobInstance.status.in_(ACTIVE_JOB_STATUSES),
        )
        .count()
    )
    if active_jobs:
        raise Conflict({
            "code": "DEVICE_HAS_ACTIVE_WORK",
            "message": f"设备有 {active_jobs} 个活跃 Job，请先 abort 或等其结束再改绑",
        })

    active_leases = (
        db.query(DeviceLease)
        .filter(
            DeviceLease.device_id == device_id,
            DeviceLease.status == _ACTIVE_LEASE,
        )
        .count()
    )
    if active_leases:
        raise Conflict({
            "code": "DEVICE_HAS_ACTIVE_WORK",
            "message": f"设备有 {active_leases} 条 ACTIVE 租约，请等租约释放后再改绑",
        })


def rebind_device(
    db: Session,
    *,
    device_id: int,
    target_host_id: str,
    reason: str,
    actor_id: Optional[int],
    actor_username: Optional[str],
    request: Optional[Any] = None,
) -> Device:
    """把设备账面归属改到目标主机（admin + 审计；幂等）。"""
    device = _locked_device(db, device_id)
    if device.retired_at is not None:
        raise Conflict({
            "code": "DEVICE_RETIRED",
            "message": "设备已退役，请先 unretire 再改绑",
        })

    if device.host_id == target_host_id:
        return device  # 幂等：目标即当前归属，原样返回、不重复审计

    host = db.get(Host, target_host_id)
    if host is None:
        raise NotFound({"code": "HOST_NOT_FOUND", "message": "目标主机不存在"})
    if host.retired_at is not None:
        raise Conflict({
            "code": "HOST_RETIRED",
            "message": "目标主机已退役，请先 unretire 再改绑",
        })

    _assert_idle(db, device_id)

    from_host_id = device.host_id
    device.host_id = target_host_id

    record_audit(
        db,
        action="rebind_device",
        resource_type="device",
        resource_id=device.id,
        details={
            "serial": device.serial,
            "from_host_id": from_host_id,
            "to_host_id": target_host_id,
            "reason": reason,
        },
        user_id=actor_id,
        username=actor_username,
        request=request,
        strict=True,
    )
    db.commit()
    db.refresh(device)
    logger.info(
        "device_rebound device=%s serial=%s %s->%s by=%s",
        device.id, device.serial, from_host_id, target_host_id, actor_username,
    )
    return device