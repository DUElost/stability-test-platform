"""Agent Job 终态完成业务逻辑（#1520 垂直切片：agent_api complete 线）。

覆盖 ``/jobs/{job_id}/complete``：终态映射、fencing 回放幂等、UNKNOWN grace
late-complete、状态机迁移、watcher 摘要、lease 释放、聚合与 post_completion
入队。路由退化为 ``ok(await complete_agent_job(...))``。

共享的 ``get_valid_runtime_lease`` 亦下沉于此（heartbeat / extend_lock 经路由
re-export 继续使用）。``resume_expired_lease_for_recovery`` 复用
``agent_recovery``。
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.audit import record_audit_async
from backend.core.metrics import (
    post_completion_enqueue_failed_total,
    record_reconciler_skip_unchanged,
    record_watcher_capability,
)
from backend.models.device_lease import DeviceLease
from backend.models.enums import JobStatus, LeaseStatus, LeaseType
from backend.models.job import JobInstance, StepTrace
from backend.models.plan_run import PlanRun
from backend.realtime.socketio_server import broadcast_plan_run_status, broadcast_run_job_update
from backend.services.aggregator import PlanAggregator
from backend.services.agent_recovery import resume_expired_lease_for_recovery
from backend.services.lease_manager import release_lease
from backend.services.state_machine import InvalidTransitionError, JobStateMachine

logger = logging.getLogger(__name__)

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


_RUN_TO_JOB: Dict[str, JobStatus] = {
    "RUNNING":   JobStatus.RUNNING,
    "FINISHED":  JobStatus.COMPLETED,
    "COMPLETED": JobStatus.COMPLETED,
    "FAILED":    JobStatus.FAILED,
    "CANCELED":  JobStatus.ABORTED,
    "CANCELLED": JobStatus.ABORTED,
    "ABORTED":   JobStatus.ABORTED,
}

class _RunCompleteIn(BaseModel):
    update: Dict[str, Any]
    artifact: Optional[Dict[str, Any]] = None
    # Watcher 摘要回填（来自 Agent JobSession.summary.to_complete_payload）
    # 字段形态参考 backend/agent/watcher/contracts.py WatcherSummaryPayload
    # 可选：watcher_id / watcher_started_at / watcher_stopped_at / watcher_capability / log_signal_count / watcher_stats
    watcher_summary: Optional[Dict[str, Any]] = None
    fencing_token: str  # ADR-0019 Phase 2b: 必填


async def get_valid_runtime_lease(
    db: AsyncSession,
    job: JobInstance,
    fencing_token: str,
    allowed_job_statuses: set[str] | None = None,
) -> Optional[DeviceLease]:
    """Validate runtime lease for token-gated operations (Phase 4b).

    Returns the valid ACTIVE lease, or None if any check fails.
    Checks (all must pass):
      1. ACTIVE lease exists for (device_id, job_id, JOB)
      2. fencing_token matches
      3. expires_at > now (B: expired lease rejects all runtime ops)
      4. job.status == RUNNING (C: whitelist, not blacklist)
    """
    now = datetime.now(timezone.utc)
    lease = (await db.execute(
        select(DeviceLease).where(
            DeviceLease.device_id == job.device_id,
            DeviceLease.job_id == job.id,
            DeviceLease.lease_type == LeaseType.JOB.value,
            DeviceLease.status == LeaseStatus.ACTIVE.value,
        )
    )).scalars().first()

    if lease is None:
        return None
    if lease.fencing_token != fencing_token:
        return None
    # B: expired lease rejects all runtime operations
    expires_at = _as_utc(lease.expires_at)
    if expires_at is None or expires_at <= now:
        return None
    # C: only RUNNING jobs may perform runtime operations
    allowed_statuses = allowed_job_statuses or {JobStatus.RUNNING.value}
    if job.status not in allowed_statuses:
        return None
    return lease

def apply_watcher_summary(job: JobInstance, summary: Dict[str, Any]) -> None:
    """把 Agent 回传的 watcher_summary 回填到 JobInstance 字段。

    字段来源契约：backend/agent/watcher/contracts.py WatcherSummaryPayload
    只在 summary 非空字段存在时覆写，保持旧字段。
    """
    started = summary.get("watcher_started_at")
    if started:
        try:
            job.watcher_started_at = datetime.fromisoformat(started.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            logger.warning("watcher_summary.watcher_started_at invalid: %r", started)

    stopped = summary.get("watcher_stopped_at")
    if stopped:
        try:
            job.watcher_stopped_at = datetime.fromisoformat(stopped.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            logger.warning("watcher_summary.watcher_stopped_at invalid: %r", stopped)

    capability = summary.get("watcher_capability")
    if capability:
        job.watcher_capability = str(capability)[:32]

    # log_signal_count 由 /log-signals 端点累加，这里不覆写
    # 但若 Agent 侧有权威计数（watcher_summary.log_signal_count），作为下限同步
    count = summary.get("log_signal_count")
    if isinstance(count, int) and count > (job.log_signal_count or 0):
        job.log_signal_count = count

def bridge_reconciler_metrics(host_id: Optional[str], summary: Dict[str, Any]) -> None:
    """M0/Task2: 把 Agent 进程内的 reconciler 计数桥接到中心 /metrics。

    Agent 没有独立的 Prometheus /metrics 暴露面,reconciler 的
    `reconciler_skip_unchanged_total` 只在 Agent 进程的本地 registry 自增、永不被抓取。
    为让 M4 监控盘可见,Agent 通过 complete 通道带出整个 Job 生命周期累计的
    `reconciler_stats.ticks_skipped_unchanged`,后端在 Job *首次*进入终态时一次性
    按该累计值自增中心计数器(每个 Job 仅贡献一次 → 计数器单调正确)。

    `reconciler_burst_mode_active` 是运行期实时 gauge(Job 结束时恒为 0),无法通过
    终态快照有意义地带出 → 仍仅 Agent 进程内,详见 §2.3 文档说明。
    """
    stats = summary.get("reconciler_stats")
    if not isinstance(stats, dict):
        return
    skipped = stats.get("ticks_skipped_unchanged")
    if isinstance(skipped, int) and skipped > 0:
        record_reconciler_skip_unchanged(str(host_id or "unknown"), amount=skipped)

async def complete_agent_job(
    db: AsyncSession,
    job_id: int,
    payload: _RunCompleteIn,
) -> dict:
    """Transition job to a terminal status."""
    job = (await db.execute(
        select(JobInstance)
        .where(JobInstance.id == job_id)
        .with_for_update()
    )).scalars().first()
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    raw = str(payload.update.get("status", "FAILED")).strip().upper()
    # #779: 未知状态串不得经 .get(..., FAILED) 伪装成真实失败。
    if raw not in _RUN_TO_JOB:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_TERMINAL_STATUS", "requested_status": raw},
        )
    target = _RUN_TO_JOB[raw]
    if target not in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.ABORTED}:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_TERMINAL_STATUS", "requested_status": raw},
        )

    completion_fact = {
        "update": payload.update,
        "artifact": payload.artifact,
        "watcher_summary": payload.watcher_summary,
    }
    payload_digest = hashlib.sha256(
        json.dumps(
            completion_fact,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    # Terminal replay is strictly read-only.  Validate against the historical
    # lease token so a stale/cross-host Agent cannot rewrite completion facts.
    already_terminal = job.status in _TERMINAL
    if already_terminal:
        historical_lease = (await db.execute(
            select(DeviceLease)
            .where(
                DeviceLease.device_id == job.device_id,
                DeviceLease.job_id == job.id,
                DeviceLease.lease_type == LeaseType.JOB.value,
            )
            .order_by(DeviceLease.id.desc())
        )).scalars().first()
        if (
            historical_lease is None
            or historical_lease.fencing_token != payload.fencing_token
        ):
            current_status = job.status
            await record_audit_async(
                db,
                action="stale_job_completion_rejected",
                resource_type="job",
                resource_id=job.id,
                details={
                    "plan_run_id": job.plan_run_id,
                    "current_status": current_status,
                    "reason": "historical_fencing_token_mismatch",
                },
                username="agent",
            )
            await db.commit()
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "STALE_COMPLETION_TOKEN",
                    "current_status": current_status,
                },
            )
        expected_digest = job.terminal_payload_digest
        if expected_digest is None:
            historical_trace = (await db.execute(
                select(StepTrace).where(
                    StepTrace.job_id == job_id,
                    StepTrace.step_id == "__job__",
                    StepTrace.event_type == "RUN_COMPLETE",
                )
            )).scalars().first()
            if historical_trace is not None and historical_trace.output:
                try:
                    historical_fact = json.loads(historical_trace.output)
                    current_legacy_fact = {
                        "update": payload.update,
                        "artifact": payload.artifact,
                    }
                    expected_digest = hashlib.sha256(
                        json.dumps(
                            historical_fact,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                    replay_digest = hashlib.sha256(
                        json.dumps(
                            current_legacy_fact,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                except (TypeError, ValueError, json.JSONDecodeError):
                    replay_digest = ""
            else:
                replay_digest = ""
        else:
            replay_digest = payload_digest
        if not expected_digest or not secrets.compare_digest(
            expected_digest, replay_digest,
        ):
            current_status = job.status
            await record_audit_async(
                db,
                action="terminal_payload_conflict",
                resource_type="job",
                resource_id=job.id,
                details={
                    "plan_run_id": job.plan_run_id,
                    "current_status": current_status,
                    "expected_digest": expected_digest,
                    "received_digest": replay_digest or payload_digest,
                },
                username="agent",
            )
            await db.commit()
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "TERMINAL_PAYLOAD_CONFLICT",
                    "current_status": current_status,
                },
            )
        current_status = job.status
        await db.rollback()
        return {"job_id": job_id, "status": current_status, "idempotent": True}
    else:
        valid_lease = None
        if job.status == JobStatus.UNKNOWN.value:
            # Explicit late-terminal reconciliation: a matching grace-held
            # token may atomically restore UNKNOWN→RUNNING before completion.
            # This preserves the terminal outbox fact without adding the
            # forbidden UNKNOWN→COMPLETED edge to the state machine.
            candidate = (await db.execute(
                select(DeviceLease)
                .where(
                    DeviceLease.device_id == job.device_id,
                    DeviceLease.job_id == job.id,
                    DeviceLease.lease_type == LeaseType.JOB.value,
                    DeviceLease.status == LeaseStatus.ACTIVE.value,
                )
                .with_for_update()
            )).scalars().first()
            if (
                candidate is not None
                and secrets.compare_digest(
                    candidate.fencing_token, payload.fencing_token,
                )
                and await resume_expired_lease_for_recovery(
                    db,
                    candidate,
                    job,
                    candidate.agent_instance_id,
                    datetime.now(timezone.utc),
                )
            ):
                JobStateMachine.transition(
                    job, JobStatus.RUNNING, "late_completion_recovery",
                )
                valid_lease = candidate
        if valid_lease is None:
            valid_lease = await get_valid_runtime_lease(
                db, job, payload.fencing_token,
            )
        if valid_lease is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "INVALID_OR_EXPIRED_FENCING_TOKEN",
                    "current_status": job.status,
                },
            )

    transition_from_status = job.status
    try:
        JobStateMachine.transition(job, target, payload.update.get("error_message") or "")
    except InvalidTransitionError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "INVALID_JOB_TRANSITION",
                "message": str(exc),
                "current_status": job.status,
                "requested_status": target.value,
            },
        ) from exc

    # 持久化一次性完成快照（log_summary + artifact），为新链路报告读取提供数据闭环。
    snapshot = {
        "update": payload.update,
        "artifact": payload.artifact,
    }
    snapshot_output = json.dumps(snapshot, ensure_ascii=False)
    now_ts = datetime.now(timezone.utc)
    existing_snapshot = (
        await db.execute(
            select(StepTrace).where(
                StepTrace.job_id == job_id,
                StepTrace.step_id == "__job__",
                StepTrace.event_type == "RUN_COMPLETE",
            )
        )
    ).scalars().first()
    if existing_snapshot is None:
        db.add(
            StepTrace(
                job_id=job_id,
                step_id="__job__",
                stage="post_process",
                status=target.value,
                event_type="RUN_COMPLETE",
                output=snapshot_output,
                error_message=payload.update.get("error_message"),
                trace_event_id=f"terminal:{job_id}:{payload_digest}",
                original_ts=now_ts,
                created_at=datetime.now(timezone.utc),
            )
        )

    job.ended_at = datetime.now(timezone.utc)
    job.terminal_payload_digest = payload_digest

    # Watcher 摘要回填（来自 Agent JobSession.summary.to_complete_payload）
    # 字段契约见 backend/agent/watcher/contracts.py WatcherSummaryPayload
    if payload.watcher_summary:
        apply_watcher_summary(job, payload.watcher_summary)

    if target == JobStatus.ABORTED:
        plan_run = (await db.execute(
            select(PlanRun)
            .where(PlanRun.id == job.plan_run_id)
            .with_for_update(key_share=True)
        )).scalars().first()
        if plan_run is not None and isinstance(plan_run.run_context, dict):
            run_context = dict(plan_run.run_context)
            abort_request = dict(run_context.get("abort_requested") or {})
            acknowledged = list(
                abort_request.get("acknowledged_job_ids") or []
            )
            if job.id not in acknowledged:
                acknowledged.append(job.id)
            abort_request["acknowledged_job_ids"] = acknowledged
            run_context["abort_requested"] = abort_request
            plan_run.run_context = run_context

    if job.status in _TERMINAL:
        # M0/Task2: 仅在首次终态桥接 reconciler 计数,避免 outbox 重试重复计数。
        if payload.watcher_summary:
            bridge_reconciler_metrics(job.host_id, payload.watcher_summary)
            # M4/T4-2: 终态时按 watcher_capability 自增一次(覆盖率监控盘);
            # 与 reconciler 桥接同处 not-already_terminal 守卫内,每 Job 仅计一次。
            record_watcher_capability(job.watcher_capability or "unknown")
        # Release before aggregation.  Chain dispatch inherits the same devices
        # and must observe them as free when the terminal transaction commits.
        released = await release_lease(db, job.device_id, job_id, LeaseType.JOB)
        if not released:
            logger.warning("release_lease_miss device=%s job=%s", job.device_id, job_id)
        await db.flush()
        await record_audit_async(
            db,
            action="job_terminalized",
            resource_type="job",
            resource_id=job.id,
            details={
                "plan_run_id": job.plan_run_id,
                "from_status": transition_from_status,
                "to_status": target.value,
                "payload_digest": payload_digest,
                "lease_released": bool(released),
            },
            username="agent",
        )
        await PlanAggregator.on_job_terminal(job, db)

    await db.commit()

    if job.status in _TERMINAL:
        # ── SocketIO push: job completed/failed → notify PlanRun subscribers ──
        await broadcast_run_job_update(job.plan_run_id, job_id, job.status)
        run = await db.get(PlanRun, job.plan_run_id)
        if run is not None and run.status in {
            "SUCCESS", "PARTIAL_SUCCESS", "FAILED",
        }:
            await broadcast_plan_run_status(run.id, run.status)

        try:
            from backend.tasks.saq_worker import get_queue
            from saq import Job as SaqJob

            await get_queue().enqueue(
                SaqJob(
                    function="post_completion_task",
                    kwargs={"job_id": job_id},
                    key=f"pc:{job_id}",
                    timeout=120,
                    retries=3,
                    retry_delay=5.0,
                    retry_backoff=True,
                )
            )
        except Exception as e:
            post_completion_enqueue_failed_total.inc()
            logger.error("post_completion enqueue failed for job %d: %s", job_id, e)

    return {"job_id": job_id, "status": job.status}

# 路由 / 既有测试用的私有名别名。
_get_valid_runtime_lease = get_valid_runtime_lease
_apply_watcher_summary = apply_watcher_summary
_bridge_reconciler_metrics = bridge_reconciler_metrics
