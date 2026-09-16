"""Agent crash-recovery 业务逻辑（#1520 垂直切片：agent_api recovery 线）。

覆盖 ``/agent/recovery/sync`` 全链路，以及 complete_job 晚到完成也复用的
``resume_expired_lease_for_recovery``。路由退化为「解析 → 调服务 → ok()」；
异常沿用 ``HTTPException``。

边界：本模块不碰 claim / heartbeat / complete 主路径（除共享的 lease grace
刷新）；Pydantic 入参模型随业务线下沉，路由侧 re-export 保持既有测试导入。
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.device_lease import DeviceLease
from backend.models.enums import JobStatus, LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun
from backend.services.aggregator import PlanAggregator
from backend.services.lease_manager import release_lease
from backend.services.plan_dispatcher_core import (
    apply_dispatch_host_watcher_admin_state_to_policy,
    extract_dispatch_host_watcher_admin_states,
)
from backend.services.plan_run_abort import abort_pending_job_ids
from backend.services.state_machine import InvalidTransitionError, JobStateMachine

logger = logging.getLogger(__name__)

_DEVICE_LOCK_LEASE_SECONDS = int(os.getenv("DEVICE_LOCK_LEASE_SECONDS", "600"))
_TERMINAL = {
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.ABORTED.value,
}


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso_or_none(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return _as_utc(dt).isoformat()




class _ActiveJobEntry(BaseModel):
    job_id: int
    device_id: int
    device_serial: Optional[str] = None
    fencing_token: str = ""


class _OutboxEntry(BaseModel):
    job_id: int
    event_type: str = "RUN_COMPLETED"


class _RecoverySyncIn(BaseModel):
    host_id: str
    agent_instance_id: str = ""
    boot_id: str = ""
    active_jobs: List[_ActiveJobEntry] = []
    pending_outbox: List[_OutboxEntry] = []


class _RecoveryAction(BaseModel):
    job_id: int
    device_id: Optional[int] = None
    action: str       # RESUME | CLEANUP | ABORT_LOCAL | UPLOAD_TERMINAL | NOOP
    fencing_token: str = ""
    device_serial: str = ""
    job_payload: Optional[Dict[str, Any]] = None
    event_type: str = ""
    reason: str = ""


class _RecoverySyncOut(BaseModel):
    data: dict  # {"actions": [...], "outbox_actions": [...]}

async def build_recovery_job_payload(
    db: AsyncSession,
    job: JobInstance,
    *,
    device_serial: str,
    fencing_token: str,
) -> Dict[str, Any]:
    """Build the minimal claim-shaped payload required for Agent resume execution.

    NOTE: PlanRun is fetched individually here (not batched with other jobs).
    Recovery is a single-job path in practice; if batch recovery is introduced later,
    consider pre-loading PlanRun rows upstream and passing dispatch_host_watcher_admin_states
    as a parameter to avoid N+1 queries.
    """
    watcher_policy = None
    dispatch_host_watcher_admin_states: Dict[str, bool] = {}
    if job.plan_run_id is not None:
        plan_run = await db.get(PlanRun, job.plan_run_id)
        if plan_run is not None:
            snapshot_plan = (
                (plan_run.plan_snapshot or {}).get("plan", {})
                if isinstance(plan_run.plan_snapshot, dict)
                else {}
            )
            watcher_policy = snapshot_plan.get("watcher_policy")
            dispatch_host_watcher_admin_states = (
                extract_dispatch_host_watcher_admin_states(plan_run.run_context)
            )
    watcher_policy = apply_dispatch_host_watcher_admin_state_to_policy(
        watcher_policy,
        host_id=job.host_id,
        dispatch_host_watcher_admin_states=dispatch_host_watcher_admin_states,
    )

    plan_run_host_id = None
    plan_run_host_total_job_count = None
    if job.plan_run_id is not None and job.host_id:
        from backend.models.plan_run import PlanRunHost as _PRH
        prh = (
            await db.execute(
                select(_PRH).where(
                    _PRH.plan_run_id == job.plan_run_id,
                    _PRH.host_id == job.host_id,
                )
            )
        ).scalar_one_or_none()
        if prh is not None:
            plan_run_host_id = prh.id
            plan_run_host_total_job_count = int(
                prh.total_job_count or prh.device_count or 0
            )

    return {
        "id": job.id,
        "plan_run_id": job.plan_run_id,
        "plan_id": job.plan_id,
        "device_id": job.device_id,
        "device_serial": device_serial,
        "host_id": job.host_id,
        "status": job.status,
        "pipeline_def": job.pipeline_def,
        "watcher_policy": watcher_policy,
        "fencing_token": fencing_token,
        "started_at": _iso_or_none(job.started_at),
        # ADR-0026 §3 (Step 5a): execution_state MUST ride in the frozen
        # resume payload — without it a recovered PATROL_SLEEP job would be
        # judged with the EXECUTING_STEP clock and vice versa.
        "execution_state": job.execution_state,
        # ADR-0026 barrier: recovered jobs still need PRH identity + peer count.
        "plan_run_host_id": plan_run_host_id,
        "plan_run_host_total_job_count": plan_run_host_total_job_count,
    }

async def resume_expired_lease_for_recovery(
    db: AsyncSession,
    lease: DeviceLease,
    job: JobInstance,
    agent_instance_id: str,
    now: datetime,
    grace_seconds: int = 300,
) -> bool:
    """Refresh an expired ACTIVE lease for UNKNOWN→RUNNING recovery (Phase 4b).

    MUST be called under row lock on both lease and job rows.
    Only succeeds when:
      - lease.status == ACTIVE (row-locked, may have been released concurrently)
      - job.status == UNKNOWN (row-locked, may have been finalized concurrently)
      - job.ended_at is within grace period (now - ended_at < grace_seconds)

    Does NOT use extend_lease() — this is the ONLY place that refreshes
    an expired lease, and only under the validated recovery preconditions.
    """

    # Re-check under row lock
    if lease.status != LeaseStatus.ACTIVE.value:
        return False
    if job.status != JobStatus.UNKNOWN.value:
        return False
    if job.ended_at is None:
        return False
    grace_deadline = now - timedelta(seconds=grace_seconds)
    if job.ended_at <= grace_deadline:
        return False

    # Refresh lease TTL — Phase 6d: device_leases is sole source of truth,
    # no projection writes to device.lock_run_id / lock_expires_at.
    new_expires_at = now + timedelta(seconds=_DEVICE_LOCK_LEASE_SECONDS)
    lease.renewed_at = now
    lease.expires_at = new_expires_at
    lease.agent_instance_id = agent_instance_id

    return True


async def rotate_recovery_lease_token(
    db: AsyncSession,
    lease: DeviceLease,
    *,
    agent_instance_id: str,
) -> str:
    """Fence the previous local worker when a new Agent instance takes over."""
    device = (await db.execute(
        select(Device)
        .where(Device.id == lease.device_id)
        .with_for_update()
    )).scalars().first()
    if device is None:
        raise HTTPException(status_code=409, detail="recovery device not found")
    device.lease_generation = int(device.lease_generation or 0) + 1
    lease.lease_generation = device.lease_generation
    lease.fencing_token = f"{lease.device_id}:{device.lease_generation}"
    lease.agent_instance_id = agent_instance_id
    lease.renewed_at = datetime.now(timezone.utc)
    return lease.fencing_token

async def sync_agent_recovery(
    db: AsyncSession,
    payload: _RecoverySyncIn,
) -> dict:
    """Agent crash-recovery state reconciliation（业务核心）。

    返回 ``{"actions": [...], "outbox_actions": [...]}``；路由负责 ``ok()`` 包装。
    docstring 语义见路由 ``recovery_sync``。
    """
    # Load host
    host = await db.get(Host, payload.host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="host not found")

    # ── Outbox actions ──
    # （#2030）必须在退役判据**之前**推导并与早返回分支共用：Agent 对空
    # `outbox_actions` 不 ack 本地 outbox（backend/agent/main.py），退役早返回
    # 若丢掉本块，终态证据会每轮重发且永不被 ack、退役前产生的 UPLOAD_TERMINAL
    # 结果也不再投递。本块只读（不改身份、不锁 lease），不构成「承接工作」。
    outbox_actions: list[_RecoveryAction] = []
    for entry in payload.pending_outbox:
        job = await db.get(JobInstance, entry.job_id)
        if job is None:
            outbox_actions.append(_RecoveryAction(
                job_id=entry.job_id,
                action="NOOP", reason="job_not_found",
            ))
        elif job.status in _TERMINAL:
            outbox_actions.append(_RecoveryAction(
                job_id=entry.job_id,
                action="NOOP", reason="already_terminal",
            ))
        else:
            outbox_actions.append(_RecoveryAction(
                job_id=entry.job_id,
                action="UPLOAD_TERMINAL", event_type=entry.event_type,
                reason="not_terminal_on_backend",
            ))

    # ADR-0038 D5bis：退役主机不得经 recovery 复活在飞作业（见 docstring）。
    # 放在身份更新**之前**：退役机即使上报新 boot_id/instance_id 也不该继续
    # 承接工作，故不写身份、不进入后续 RESUME 判定，直接引导本地停止。
    if host.retired_at is not None:
        logger.info("recovery_sync_skipped_host_retired host=%s", payload.host_id)
        return {
            "actions": [
                _RecoveryAction(
                    job_id=entry.job_id,
                    device_id=entry.device_id,
                    action="ABORT_LOCAL",
                    reason="host_retired",
                ).model_dump()
                for entry in payload.active_jobs
            ],
            "outbox_actions": [a.model_dump() for a in outbox_actions],
        }

    # D1: snapshot previous_boot_id before overwriting
    previous_boot_id = host.boot_id

    # Update host identity
    if payload.boot_id:
        host.boot_id = payload.boot_id
    if payload.agent_instance_id:
        host.last_agent_instance_id = payload.agent_instance_id

    now = datetime.now(timezone.utc)

    # ── Active job actions ──
    _recovery_grace_seconds = 300  # UNKNOWN grace for recovery, matches watchdog
    job_actions: list[_RecoveryAction] = []
    for entry in payload.active_jobs:
        # Lock the complete ownership tuple.  Shared AGENT_SECRET authenticates
        # an Agent process, not a host/job relationship; the fencing token and
        # relational checks below establish that relationship.
        # #2015（I1 共享行加锁全序）：先锁 job 行、再锁 lease 行——与
        # complete_job / extend_leases_batch / reconciler 的 Job → Lease 同序。
        # 原先先锁 lease 再锁 job，与续租 tick（Job 锁内 CAS device_leases）在
        # 同一 (job, lease) 对上反向：Agent 重启的 recovery 恰落在续租窗口内
        # 即可成环（09-15 生产死锁 60 次的环，PG 服务端日志定位）。本路由只
        # 交换两条 SELECT 的次序；下方全部校验（ownership/fencing/boot）与
        # 动作判定不变——job 不存在时 ownership 检查的结论与原先一致。
        job = (await db.execute(
            select(JobInstance)
            .where(JobInstance.id == entry.job_id)
            .with_for_update()
        )).scalars().first()
        lease = (await db.execute(
            select(DeviceLease).where(
                DeviceLease.device_id == entry.device_id,
                DeviceLease.job_id == entry.job_id,
                DeviceLease.lease_type == LeaseType.JOB.value,
                DeviceLease.status == LeaseStatus.ACTIVE.value,
            ).with_for_update()  # Phase 4b: lock lease row against concurrent Reconciler
        )).scalars().first()

        if lease is None:
            # Also check for RELEASED/EXPIRED lease
            any_lease = (await db.execute(
                select(DeviceLease).where(
                    DeviceLease.device_id == entry.device_id,
                    DeviceLease.job_id == entry.job_id,
                    DeviceLease.lease_type == LeaseType.JOB.value,
                )
            )).scalars().first()
            if any_lease is None:
                job_actions.append(_RecoveryAction(
                    job_id=entry.job_id, device_id=entry.device_id,
                    action="ABORT_LOCAL", reason="no_active_lease",
                ))
            else:
                job_actions.append(_RecoveryAction(
                    job_id=entry.job_id, device_id=entry.device_id,
                    action="ABORT_LOCAL", reason="lease_not_active",
                ))
            continue

        device = (await db.execute(
            select(Device)
            .where(Device.id == entry.device_id)
            .with_for_update()
        )).scalars().first()
        actual_serial = ((device.serial or "").strip() if device is not None else "")
        reported_serial = (entry.device_serial or "").strip()
        ownership_valid = (
            job is not None
            and device is not None
            and job.device_id == entry.device_id
            and job.host_id == payload.host_id
            and lease.host_id == payload.host_id
            and device.host_id == payload.host_id
            and bool(entry.fencing_token)
            and secrets.compare_digest(entry.fencing_token, lease.fencing_token)
            and (not reported_serial or actual_serial == reported_serial)
        )
        if not ownership_valid:
            logger.warning(
                "recovery_sync_ownership_rejected host=%s job=%s device=%s",
                payload.host_id, entry.job_id, entry.device_id,
            )
            job_actions.append(_RecoveryAction(
                job_id=entry.job_id,
                device_id=entry.device_id,
                action="ABORT_LOCAL",
                reason="recovery_ownership_mismatch",
            ))
            continue

        lease_agent_id = lease.agent_instance_id or ""
        boot_matches = (
            previous_boot_id == payload.boot_id
            if previous_boot_id and payload.boot_id
            else lease_agent_id == payload.agent_instance_id
        )

        if not boot_matches:
            # Host reboot invalidates the local process.  Finalize through the
            # same terminal side effects expected from normal completion.
            try:
                JobStateMachine.transition(
                    job, JobStatus.FAILED, "recovery_cleanup_boot_mismatch",
                )
                job.ended_at = now
            except InvalidTransitionError:
                pass
            await release_lease(db, entry.device_id, entry.job_id, LeaseType.JOB)
            await db.flush()
            if job.status == JobStatus.FAILED.value:
                await PlanAggregator.on_job_terminal(job, db)
            job_actions.append(_RecoveryAction(
                job_id=entry.job_id, device_id=entry.device_id,
                action="CLEANUP", reason="boot_id_mismatch",
            ))
            continue

        # #990 / R06-F05: durable abort intent forbids RESUME (and same-instance
        # NOOP that would leave a live worker running). Steer Agent to local stop
        # confirmation; keep the lease until /complete ABORTED ACK or abort reaper.
        plan_run = await db.get(PlanRun, job.plan_run_id)
        run_ctx = (
            plan_run.run_context
            if plan_run is not None and isinstance(plan_run.run_context, dict)
            else {}
        )
        # #2270：主体感知——host 级 abort 只覆盖该 host 的 job；按「键存在」判定会
        # 把同 run 旁主机的 job 也强推 ABORT_LOCAL（误杀从未被请求中止的主机）。
        if job.id in abort_pending_job_ids(run_ctx, [(job.id, job.host_id)]):
            if lease_agent_id != payload.agent_instance_id:
                await rotate_recovery_lease_token(
                    db, lease, agent_instance_id=payload.agent_instance_id,
                )
            job_actions.append(_RecoveryAction(
                job_id=entry.job_id,
                device_id=entry.device_id,
                action="ABORT_LOCAL",
                fencing_token=lease.fencing_token,
                device_serial=actual_serial,
                reason="abort_requested",
            ))
            continue

        if job.status == JobStatus.UNKNOWN.value:
            # Phase 4b: UNKNOWN→RUNNING resurrection (within grace)
            resumed = await resume_expired_lease_for_recovery(
                db, lease, job, payload.agent_instance_id, now, _recovery_grace_seconds,
            )
            if resumed:
                if lease_agent_id != payload.agent_instance_id:
                    await rotate_recovery_lease_token(
                        db, lease, agent_instance_id=payload.agent_instance_id,
                    )
                try:
                    JobStateMachine.transition(job, JobStatus.RUNNING, "recovery_resume_unknown")
                except InvalidTransitionError:
                    pass
                job_payload = await build_recovery_job_payload(
                    db,
                    job,
                    device_serial=actual_serial,
                    fencing_token=lease.fencing_token,
                )
                job_actions.append(_RecoveryAction(
                    job_id=entry.job_id, device_id=entry.device_id,
                    action="RESUME", fencing_token=lease.fencing_token,
                    device_serial=actual_serial,
                    job_payload=job_payload,
                    reason="recovery_resume_unknown",
                ))
            else:
                # Grace expired or state changed under lock
                await release_lease(db, entry.device_id, entry.job_id, LeaseType.JOB)
                job_actions.append(_RecoveryAction(
                    job_id=entry.job_id, device_id=entry.device_id,
                    action="CLEANUP", reason="unknown_grace_expired",
                ))
            continue

        if job.status in _TERMINAL:
            # D5: terminal job with lingering ACTIVE lease → release, ABORT_LOCAL
            await release_lease(db, entry.device_id, entry.job_id, LeaseType.JOB)
            job_actions.append(_RecoveryAction(
                job_id=entry.job_id, device_id=entry.device_id,
                action="ABORT_LOCAL", reason="terminal_job_active_lease",
            ))
            continue

        if job.status != JobStatus.RUNNING.value:
            job_actions.append(_RecoveryAction(
                job_id=entry.job_id, device_id=entry.device_id,
                action="ABORT_LOCAL", reason=f"job_not_resumable:{job.status}",
            ))
            continue

        # Repeated sync from the same live instance must not launch a second
        # local worker.  A new instance on the same boot receives a rotated
        # token so the old worker is fenced before RESUME is returned.
        if lease_agent_id == payload.agent_instance_id:
            job_actions.append(_RecoveryAction(
                job_id=entry.job_id,
                device_id=entry.device_id,
                action="NOOP",
                fencing_token=lease.fencing_token,
                device_serial=actual_serial,
                reason="same_instance_worker_already_owned",
            ))
            continue

        await rotate_recovery_lease_token(
            db, lease, agent_instance_id=payload.agent_instance_id,
        )
        reason = "same_boot_instance_takeover"

        job_payload = await build_recovery_job_payload(
            db,
            job,
            device_serial=actual_serial,
            fencing_token=lease.fencing_token,
        )
        job_actions.append(_RecoveryAction(
            job_id=entry.job_id, device_id=entry.device_id,
            action="RESUME", fencing_token=lease.fencing_token,
            device_serial=actual_serial,
            job_payload=job_payload,
            reason=reason,
        ))

    await db.commit()

    logger.info(
        "recovery_sync host=%s jobs=%d outbox=%d actions_job=%d actions_outbox=%d",
        payload.host_id,
        len(payload.active_jobs), len(payload.pending_outbox),
        len(job_actions), len(outbox_actions),
    )

    return {
        "actions": [a.model_dump() for a in job_actions],
        "outbox_actions": [a.model_dump() for a in outbox_actions],
    }


# 路由 / 既有测试用的私有名别名（行为不变）。
_build_recovery_job_payload = build_recovery_job_payload
_resume_expired_lease_for_recovery = resume_expired_lease_for_recovery
_rotate_recovery_lease_token = rotate_recovery_lease_token
