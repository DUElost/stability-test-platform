"""设备改绑（host_id 人工改写）业务逻辑（#3892 / ADR-0063 D7 配套）。

背景：`device.host_id` 的唯一自动写路径是心跳（`heartbeat.py`，设备须经 ADB
上报 online）。vbus 断电刷机的设备在刷机前是 powered_off（不上报 ADB），无法
靠心跳改绑；而派发准入 `device_host_drift` 与 prepare 分组都按当前 `host_id`
判定——「先改绑再刷」因此卡死。本服务提供 admin 入口，把设备账面归属改到目标
主机，补齐这条人工路径。

语义（与退役家族同惯例）：

- 行锁 device（`FOR UPDATE`）+ 锁内复检；目标主机再取行锁（复用
  `host_retirement._locked_host`）并在锁内复检 `retired_at`，与写入同事务——
  锁序 device→host，单锁非循环；
- 前置：设备**在役**、设备**ADB 缺席且静默满一个心跳超时**、目标主机**存在且
  在役**、设备**无活跃 Job / 无 ACTIVE 租约**（不顺带中止在跑测试，也不把租约
  从一台主机抽走）；同归属的幂等早返回在全部前置之后；
- 只改 `host_id`，**不改写 `status`**（心跳所有）、不删历史行；
- 事件真源是 `audit_logs`——审计 fail-closed（`record_audit(strict=True)`）。

静默期（#3892 R1 B4，规划者裁决「乙」）：`last_seen` 只在 serial 进入心跳的
ADB 枚举时更新；`adb_connected` 是最近一次枚举的连接标志。改绑只在无当前/
近期 ADB 观测时放行——否则改绑前已生成、改绑后到达的旧主机 ADB 报文会把归属
改回（复核 PR #3944 的 B4 反例）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.job_timeout_config import HOST_HEARTBEAT_TIMEOUT_SECONDS
from backend.models.device_lease import DeviceLease
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.services.audit_writer import record_audit
from backend.services.errors import Conflict, NotFound
from backend.services.host_retirement import _locked_host
from backend.services.host_upgrade_gate import ACTIVE_JOB_STATUSES

logger = logging.getLogger(__name__)

_ACTIVE_LEASE = "ACTIVE"

#: 改绑静默期：复用主机心跳超时（生产 300s），不新增旋钮。设备必须在最近一次
#: ADB 观测之后静默满这个时长才允许人工改绑，避免在途的旧主机 ADB 报文在改绑后
#: 把归属改回（#3892 R1 B4）。
_QUIET_PERIOD = timedelta(seconds=HOST_HEARTBEAT_TIMEOUT_SECONDS)


def _utcnow() -> datetime:
    """当前 UTC 时间（测试用 monkeypatch 固定，便于验证静默期边界）。"""
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """把 `last_seen` 归一为 aware UTC；naive 值按 UTC 解释（PG 驱动偶见）。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _locked_device(db: Session, device_id: int) -> Device:
    device = (
        db.execute(
            select(Device)
            .where(Device.id == device_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalars().first()
    if device is None:
        raise NotFound({"code": "DEVICE_NOT_FOUND", "message": "设备不存在"})
    return device


def _assert_quiet(device: Device, *, now: Optional[datetime] = None) -> None:
    """B4 前置：ADB 缺席且静默满 QUIET_PERIOD，否则 409 DEVICE_NOT_QUIET。

    `last_seen` 只在 serial 进入心跳的 ADB 枚举时更新（heartbeat.py），仅有
    USB 槽位在位观测的心跳不会更新它；`adb_connected` 是最近一次枚举的连接
    标志。两者任一显示「当前/近期有 ADB 观测」就不允许改绑。
    """
    current = now or _utcnow()
    quiet_seconds = int(_QUIET_PERIOD.total_seconds())
    if device.adb_connected is True:
        raise Conflict({
            "code": "DEVICE_NOT_QUIET",
            "message": (
                f"设备不满足改绑静默期：须 ADB 缺席且静默 \u2265{quiet_seconds}s"
                "（避免迟到心跳把归属改回）"
            ),
        })
    if device.last_seen is not None and _as_utc(device.last_seen) >= current - _QUIET_PERIOD:
        raise Conflict({
            "code": "DEVICE_NOT_QUIET",
            "message": (
                f"设备不满足改绑静默期：须 ADB 缺席且静默 \u2265{quiet_seconds}s"
                "（避免迟到心跳把归属改回）"
            ),
        })


def _assert_target_host_available(db: Session, target_host_id: str) -> None:
    """目标主机快速前置：存在且在役（最终以 _locked_host 锁内复检为准）。"""
    row = db.execute(
        select(Host.id, Host.retired_at).where(Host.id == target_host_id)
    ).first()
    if row is None:
        raise NotFound({"code": "HOST_NOT_FOUND", "message": "目标主机不存在"})
    if row.retired_at is not None:
        raise Conflict({
            "code": "HOST_RETIRED",
            "message": "目标主机已退役，请先 unretire 再改绑",
        })


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

    _assert_quiet(device)
    _assert_target_host_available(db, target_host_id)
    _assert_idle(db, device_id)

    # B2：幂等早返回必须在全部前置之后——同归属带阻断项要返回对应 409，
    # 不能因为「目标即当前归属」跳过检查。
    if device.host_id == target_host_id:
        return device

    # B1：目标主机行锁 + 锁内复检。锁与写入同事务；退役事务（_locked_host）
    # 要么先提交（本处读到 retired_at → 409），要么在本事务提交后才拿到锁。
    try:
        host = _locked_host(db, target_host_id)
    except NotFound:
        raise NotFound({"code": "HOST_NOT_FOUND", "message": "目标主机不存在"}) from None
    # 同一 Session 可能已加载过该 Host（快速前置/测试夹具）：锁内重读，避免
    # 身份映射把复检做成旧值。
    db.refresh(host)
    if host.retired_at is not None:
        raise Conflict({
            "code": "HOST_RETIRED",
            "message": "目标主机已退役，请先 unretire 再改绑",
        })

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
