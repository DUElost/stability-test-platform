"""设备退役 / 解除退役业务逻辑（ADR-0057 D1–D3/D6；#2962 B）。

语义（ADR-0057 v1.0 Accepted，E1–E5 全部采起草取向）：

- **D1**：`device.retired_at IS NOT NULL` 即退役；退役**不改写 `status`**
  （心跳所有，复用会被下一拍静默撤销），也不删任何历史行（约束 1.3）；
  事件真源是 `audit_logs`——审计 fail-closed（`record_audit(strict=True)`）。
- **D2/E2**：前置 = **无活跃 Job、无 ACTIVE 租约**，否则 409（不顺带中止在跑
  测试）；锁序与 claim 一致（先锁 device 行 `FOR UPDATE`，锁内复检）；
  admin 入口；幂等（重复 retire/unretire 返回现状、不重复审计）；
  unretire 清 `retired_at`，`retired_by`/`retire_reason` 保留最近一次痕迹。
- **D3/E1**：已退役设备重新出现在心跳里 → **保持退役**（如实记录事实列），
  只发**一次**告警（去重载体 `retire_alerted_at`；`unretire → retire` 重新计轮）。
- **D2 批量**：`retire_devices_batch` 逐台独立事务，任一台失败不影响其他台。
- **不改写 status / 不自动 unretire**：episode 判定由本模块纯函数承担，
  两个心跳端点共用（对齐 ADR-0038 D4 的主机侧实现）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.device_lease import DeviceLease
from backend.models.host import Device
from backend.models.job import JobInstance
from backend.services.audit_writer import record_audit
from backend.services.errors import Conflict, NotFound
from backend.services.host_upgrade_gate import ACTIVE_JOB_STATUSES

logger = logging.getLogger(__name__)

#: 租约前置：只有 ACTIVE 租约阻塞退役（EXPIRED/RELEASED 是历史）。
_ACTIVE_LEASE = "ACTIVE"


def _locked_device(db: Session, device_id: int) -> Device:
    """取设备并持行锁（与 claim / 租约检查同事务，杜绝「复检→写入」窗口）。"""
    device = (
        db.execute(select(Device).where(Device.id == device_id).with_for_update())
    ).scalars().first()
    if device is None:
        raise NotFound("device not found")
    return device


def _assert_no_active_work(db: Session, device_id: int) -> None:
    """E2 前置：活跃 Job 或 ACTIVE 租约即 409。

    刻意**不检查** `status`（离线设备同样允许退役）与历史行（不删不阻断）。
    """
    active_jobs = (
        db.query(JobInstance)
        .filter(
            JobInstance.device_id == device_id,
            JobInstance.status.in_(ACTIVE_JOB_STATUSES),
        )
        .count()
    )
    if active_jobs:
        raise Conflict(
            f"设备有 {active_jobs} 个活跃 Job，请先 abort 或等其结束再退役"
        )

    active_leases = (
        db.query(DeviceLease)
        .filter(
            DeviceLease.device_id == device_id,
            DeviceLease.status == _ACTIVE_LEASE,
        )
        .count()
    )
    if active_leases:
        raise Conflict(
            f"设备有 {active_leases} 条 ACTIVE 租约，请等租约释放或按既有流程回收后再退役"
        )


def should_alert_retired_device_heartbeat(
    device: Device,
    *,
    prev_status: Optional[str],
    now: Optional[datetime] = None,
) -> bool:
    """E1/D3：已退役设备又心跳 → **单次告警**（与主机侧同款计轮规则）。

    纯属性函数（不触 Session API）；调用方负责随本拍心跳提交（戳落在退役
    周期内，重启/重试不重复响）。持续上报只响一次；「离线/降级 → ONLINE」
    的恢复拍视为新一轮 episode（清戳重响），避免「长时间静默后回场」被
    既有旧戳吞掉。
    """
    if device.retired_at is None:
        return False
    if prev_status not in (None, "ONLINE"):
        device.retire_alerted_at = None
    if device.retire_alerted_at is not None:
        return False
    device.retire_alerted_at = now or datetime.now(timezone.utc)
    return True


def retired_heartbeat_context(device: Device, *, host_id: Optional[str] = None) -> dict:
    """告警上下文：逻辑事件键 `(device.id, retired_at, event_type)`（跨重试幂等）。"""
    retired_iso = device.retired_at.isoformat() if device.retired_at else ""
    return {
        "device_id": device.id,
        "device_serial": device.serial,
        "host_id": host_id if host_id is not None else device.host_id,
        "retired_at": retired_iso or None,
        "retired_by": device.retired_by,
        "retire_reason": device.retire_reason,
        "event_key": f"device:{device.id}|{retired_iso}|DEVICE_RETIRED_HEARTBEAT",
    }


def _snapshot(device: Device) -> dict:
    """审计 before/after 快照（who/when/reason + 归属，换 host 漂移可复盘）。"""
    return {
        "serial": device.serial,
        "host_id": device.host_id,
        "status": device.status,
        "retired_at": device.retired_at.isoformat() if device.retired_at else None,
        "retired_by": device.retired_by,
        "retire_reason": device.retire_reason,
    }


def retire_device(
    db: Session,
    *,
    device_id: int,
    reason: str,
    actor_id: Optional[int],
    actor_username: Optional[str],
    request: Optional[Any] = None,
) -> Device:
    """把设备标记为退役（admin + 审计；幂等）。"""
    device = _locked_device(db, device_id)
    if device.retired_at is not None:
        # 幂等：已是退役态则原样返回——不重写 who/when/reason，也不重复审计。
        return device

    _assert_no_active_work(db, device_id)

    before = _snapshot(device)
    device.retired_at = datetime.now(timezone.utc)
    device.retired_by = (actor_username or "").strip()[:128] or None
    device.retire_reason = reason
    # 新生命周期开始：允许 D3 的「已退役但仍在心跳」告警再响一次。
    device.retire_alerted_at = None

    record_audit(
        db,
        action="retire_device",
        resource_type="device",
        resource_id=device.id,
        details={"reason": reason, "before": before, "after": _snapshot(device)},
        user_id=actor_id,
        username=actor_username,
        request=request,
        strict=True,  # D1：审计是事件真源，写不进去就不许改状态
    )
    db.commit()
    db.refresh(device)
    logger.info(
        "device_retired device=%s serial=%s by=%s", device.id, device.serial, device.retired_by
    )
    return device


def unretire_device(
    db: Session,
    *,
    device_id: int,
    reason: Optional[str],
    actor_id: Optional[int],
    actor_username: Optional[str],
    request: Optional[Any] = None,
) -> Device:
    """解除退役（admin + 审计；无前置；幂等）。

    `retired_by` / `retire_reason` 保留为最近一次退役痕迹（审计另有快照）；
    `reason` 是**解除原因**，只进审计。
    """
    device = _locked_device(db, device_id)
    if device.retired_at is None:
        return device  # 幂等：本就在役则原样返回

    before = _snapshot(device)
    device.retired_at = None
    device.retire_alerted_at = None

    details: dict[str, Any] = {"before": before, "after": _snapshot(device)}
    if reason:
        details["reason"] = reason
    record_audit(
        db,
        action="unretire_device",
        resource_type="device",
        resource_id=device.id,
        details=details,
        user_id=actor_id,
        username=actor_username,
        request=request,
        strict=True,
    )
    db.commit()
    db.refresh(device)
    logger.info(
        "device_unretired device=%s serial=%s by=%s", device.id, device.serial, actor_username
    )
    return device


def retire_devices_batch(
    db: Session,
    *,
    device_ids: list[int],
    reason: str,
    actor_id: Optional[int],
    actor_username: Optional[str],
    request: Optional[Any] = None,
) -> list[dict]:
    """批量退役：逐台独立事务，**任一台失败不影响其他台**（ADR-0057 D2）。

    返回逐台结果 ``[{device_id, serial, status, error?}]``：
    ``status`` ∈ ``retired``（本轮改态）/ ``already_retired``（幂等跳过）/
    ``not_found`` / ``conflict``（E2 前置）/ ``failed``（未知异常）。

    只回传必要字段（serial 便于人核对清单）；审计在每台事务内 fail-closed，
    失败的那台按失败处理、不写状态。
    """
    results: list[dict] = []
    seen: set[int] = set()
    for raw_id in device_ids:
        try:
            device_id = int(raw_id)
        except (TypeError, ValueError):
            results.append(
                {"device_id": raw_id, "serial": None, "status": "not_found"}
            )
            continue
        if device_id in seen:
            continue  # 去重：重复 id 只处理一次
        seen.add(device_id)

        # 先取快照（不改态）：既用于 not_found，也用于区分幂等跳过与本轮改态。
        exists = (
            db.query(Device.id, Device.serial, Device.retired_at)
            .filter(Device.id == device_id)
            .first()
        )
        if exists is None:
            results.append({"device_id": device_id, "serial": None, "status": "not_found"})
            continue
        was_retired = exists.retired_at is not None

        try:
            device = retire_device(
                db,
                device_id=device_id,
                reason=reason,
                actor_id=actor_id,
                actor_username=actor_username,
                request=request,
            )
            results.append({
                "device_id": device_id,
                "serial": device.serial,
                "status": "already_retired" if was_retired else "retired",
            })
        except NotFound:
            results.append({"device_id": device_id, "serial": None, "status": "not_found"})
        except Conflict as exc:
            db.rollback()
            results.append({
                "device_id": device_id,
                "serial": exists.serial,
                "status": "conflict",
                "error": str(exc),
            })
        except Exception as exc:  # noqa: BLE001 - 逐台隔离，不让一台炸整批
            db.rollback()
            logger.warning(
                "device_retire_batch_item_failed device=%s err=%s", device_id, exc,
                exc_info=True,
            )
            results.append({
                "device_id": device_id,
                "serial": exists.serial,
                "status": "failed",
                "error": str(exc),
            })
    return results
