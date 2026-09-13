"""主机退役 / 解除退役业务逻辑（ADR-0038 D2/D6；#1801 = 实现 ②/6）。

语义（ADR-0038 v0.2 Accepted）：

- **D1**：`retired_at IS NOT NULL` 即退役；退役不改写 `status`（心跳所有），
  也不删任何历史行（不变量 2）；
- **D2**：前置 **Cordon** = 无活跃 Job + 无在途 PlanRun 引用（含 QUEUED）；
  **`status` 不进前置**（离线主机也应能退役）；**不检查 DeviceLease**——退役
  不删行，租约由 `device_lease_reconciler` 按到期/liveness 回收；把租约塞进
  前置会让「退役一台正被租用的主机」变成不可完成动作，而退役语义恰恰允许
  带着在租/历史状态退出使用（代码注释同此，见 `_assert_no_inflight_work`）；
- **锁序**：与 claim（`agent_api._claim_jobs_for_host`）一致——先对 `host` 行
  `FOR UPDATE`，再在锁内复检前置，杜绝「复检通过 → claim 先落地 → retire 覆盖」
  的竞态；
- **幂等**：重复 retire / unretire 返回一致结果（不改写历史、不重复审计）；
- **unretire 写回**：清 `retired_at`，`retired_by` / `retire_reason` **保留为
  最近一次退役痕迹**（审计里另有 before/after 快照）；
- **审计 fail-closed**：`audit_logs` 是事件真源，写不进去就不许改状态——
  走 `record_audit(strict=True)`（跳过缺表降级），审计异常直接使事务失败。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.audit import record_audit
from backend.models.host import Host
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun, PlanRunHost
from backend.services.host_upgrade_gate import ACTIVE_JOB_STATUSES

logger = logging.getLogger(__name__)

#: Cordon 判据：在途 PlanRun（与派发/容量口径一致；含 QUEUED 未准入的引用）
_INFLIGHT_RUN_STATUSES = ("QUEUED", "PRECHECK", "RUNNING")


def _locked_host(db: Session, host_id: str) -> Host:
    """取主机并持行锁（与 claim 的 `select(Host)...with_for_update` 同序同锁）。

    锁在事务提交时释放；本函数之后的复检、状态写入与审计同处一个事务，
    因此「复检 → 写入」之间不会有并发的 claim/派发插入。
    """
    host = (
        db.execute(select(Host).where(Host.id == host_id).with_for_update())
    ).scalars().first()
    if host is None:
        raise HTTPException(status_code=404, detail="host not found")
    return host


def _assert_no_inflight_work(db: Session, host_id: str) -> None:
    """Cordon 前置（D2）：活跃 Job 或 在途 PlanRun 引用即 409。

    刻意**不检查**：
    - ``status``（D2 明示不进前置）——离线/降级主机同样允许退役；
    - ``DeviceLease``——退役不删设备行/租约行，租约由 reconciler 回收；
      纳入前置会让「退役正被租用的主机」变成不可完成动作，与本 ADR 允许
      「带着在租状态退出使用」的语义冲突。
    """
    active_jobs = (
        db.query(JobInstance)
        .filter(
            JobInstance.host_id == host_id,
            JobInstance.status.in_(ACTIVE_JOB_STATUSES),
        )
        .count()
    )
    if active_jobs:
        raise HTTPException(
            status_code=409,
            detail=f"主机有 {active_jobs} 个活跃 Job，请先 abort 或等其结束再退役",
        )

    inflight_runs = (
        db.query(PlanRunHost.id)
        .join(PlanRun, PlanRun.id == PlanRunHost.plan_run_id)
        .filter(
            PlanRunHost.host_id == host_id,
            PlanRun.status.in_(_INFLIGHT_RUN_STATUSES),
        )
        .count()
    )
    if inflight_runs:
        raise HTTPException(
            status_code=409,
            detail=(
                f"主机被 {inflight_runs} 个在途 PlanRun 引用"
                "（QUEUED/PRECHECK/RUNNING），请先等其结束或取消再退役"
            ),
        )


def should_alert_retired_heartbeat(
    host: Host,
    *,
    prev_status: Optional[str],
    now: Optional[datetime] = None,
) -> bool:
    """ADR-0038 D4：退役主机又心跳 → **单次告警**（去重载体 `retire_alerted_at`）。

    两条心跳端点（权威 `/api/v1/heartbeat`、轻量 `/api/v1/agent/heartbeat`）
    **共用本判据**（ADR §1.2 的「共用检测」选项），不各自复制规则。

    计轮规则：
    - 非退役主机 → False；
    - 退役 + `retire_alerted_at` 为空（本周期首拍）→ True 并打戳；
    - 退役 + 已有戳：仅当本次是「离线/降级 → ONLINE」的**恢复拍**时视为新
      episode（清零重打戳 → True）；持续 ONLINE 的后续拍 → False（持续 N 拍
      也只响一次）；
    - `unretire → retire` 重新计轮（② 的 unretire 负责清零，见该函数）。

    纯属性函数（不触 Session API），供 sync / async 两条路径复用；
    调用方负责随本拍心跳一起提交（戳落在退役周期内，重启/重试不重复响）。
    """
    if host.retired_at is None:
        return False
    if prev_status not in (None, "ONLINE"):
        # 离线/降级 → ONLINE = 新一轮「已退役但仍在心跳」episode
        host.retire_alerted_at = None
    if host.retire_alerted_at is not None:
        return False
    host.retire_alerted_at = now or datetime.now(timezone.utc)
    return True


def retired_heartbeat_context(host: Host) -> dict:
    """告警上下文：携带逻辑事件键 `(host.id, retired_at, event_type)`。

    该键供下游（通知去重 / 审计复盘）做跨重试、跨进程的幂等判据；
    `retire_alerted_at` 是行内的去重载体，两者互补。
    """
    retired_iso = host.retired_at.isoformat() if host.retired_at else ""
    return {
        "host_id": host.id,
        "hostname": host.hostname,
        "retired_at": retired_iso or None,
        "retired_by": host.retired_by,
        "retire_reason": host.retire_reason,
        "event_key": f"{host.id}|{retired_iso}|HOST_RETIRED_HEARTBEAT",
    }


def _snapshot(host: Host) -> dict:
    """审计 before/after 快照（D6：含身份当前值，换机/心跳漂移可复盘）。"""
    return {
        "status": host.status,
        "retired_at": host.retired_at.isoformat() if host.retired_at else None,
        "retired_by": host.retired_by,
        "retire_reason": host.retire_reason,
        "boot_id": host.boot_id,
        "agent_instance_id": host.last_agent_instance_id,
    }


def retire_host(
    db: Session,
    *,
    host_id: str,
    reason: str,
    actor_id: Optional[int],
    actor_username: Optional[str],
    request: Optional[Request] = None,
) -> Host:
    """把主机标记为退役（admin + 审计；幂等）。"""
    host = _locked_host(db, host_id)
    if host.retired_at is not None:
        # 幂等：已是退役态则原样返回——不重写 who/when/reason，也不重复审计。
        return host

    _assert_no_inflight_work(db, host_id)

    before = _snapshot(host)
    host.retired_at = datetime.now(timezone.utc)
    host.retired_by = (actor_username or "").strip()[:128] or None
    host.retire_reason = reason
    # 新生命周期开始：允许 ⑤ 的「已退役但仍在心跳」告警按主机再响一次。
    host.retire_alerted_at = None

    record_audit(
        db,
        action="retire_host",
        resource_type="host",
        resource_id=host.id,
        details={"reason": reason, "before": before, "after": _snapshot(host)},
        user_id=actor_id,
        username=actor_username,
        request=request,
        strict=True,  # D2：审计是事件真源，写不进去就不许改状态
    )
    db.commit()
    db.refresh(host)
    logger.info("host_retired host=%s by=%s", host.id, host.retired_by)
    return host


def unretire_host(
    db: Session,
    *,
    host_id: str,
    reason: str,
    actor_id: Optional[int],
    actor_username: Optional[str],
    request: Optional[Request] = None,
) -> Host:
    """解除退役（admin + 审计；无前置；幂等）。

    ``retired_by`` / ``retire_reason`` 保留为最近一次退役痕迹（审计另有
    before/after 快照）——本函数的 ``reason`` 是**解除原因**，只进审计。
    """
    host = _locked_host(db, host_id)
    if host.retired_at is None:
        return host  # 幂等：本就在用则原样返回

    before = _snapshot(host)
    host.retired_at = None
    host.retire_alerted_at = None

    record_audit(
        db,
        action="unretire_host",
        resource_type="host",
        resource_id=host.id,
        details={"reason": reason, "before": before, "after": _snapshot(host)},
        user_id=actor_id,
        username=actor_username,
        request=request,
        strict=True,
    )
    db.commit()
    db.refresh(host)
    logger.info("host_unretired host=%s by=%s", host.id, actor_username)
    return host
