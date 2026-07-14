"""ADR-0021 — PlanRun abort service.

Independent of the dispatch gate.  ``abort_plan_run`` is the single entry
point used by both the public ``POST /api/v1/plan-runs/{id}/abort`` API
and the host hot-update flow (``abort_running_jobs=true``).

Termination contract:

- PRECHECK / SYNCING / not-yet-dispatched RUNNING:
  - mark PlanRun status='FAILED' + result_summary={precheck_failed: True,
    reason: 'aborted_by_user'}
  - run_context.precheck.final_result='aborted'
  - no jobs to release
- RUNNING with active jobs:
  - PENDING jobs → status=ABORTED inline
  - RUNNING jobs keep their ACTIVE lease, receive an abort control command,
    terminate the process tree, then report ABORTED through the canonical
    completion endpoint
  - unresponsive agents move to UNKNOWN/quarantine; the device is never
    reallocated while the old process may still be alive
- already terminal: no-op, returns False

Always writes audit_log(action='abort_plan_run').

The function is **non-blocking**: it does not wait for agents to respond.
Frontend re-renders via SocketIO room ``plan_run:{id}``.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from backend.realtime.socketio_server import schedule_emit
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.core.audit import record_audit
from backend.core.job_timeout_config import ABORT_ACK_GRACE_SECONDS
from backend.models.enums import JobStatus, PlanRunStatus
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun
from backend.services.plan_run_aggregation import apply_plan_run_aggregation
from backend.services.dedup_scan import should_trigger_dedup, enqueue_dedup_terminal_sync
from backend.services.state_machine import PlanRunStateMachine

logger = logging.getLogger(__name__)


_TERMINAL_PLAN_RUN_STATUSES = {
    "SUCCESS",
    "PARTIAL_SUCCESS",
    "FAILED",
    "DEGRADED",
}

_TERMINAL_JOB_STATUSES = {
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.ABORTED.value,
}


class PlanRunAbortError(Exception):
    """Raised when an abort is rejected for state-machine reasons."""


def abort_plan_run(
    plan_run_id: int,
    *,
    db: Session,
    reason: str = "aborted_by_user",
    triggered_by: Optional[str] = None,
    audit_user_id: Optional[int] = None,
    audit_username: Optional[str] = None,
    audit_action: str = "abort_plan_run",
) -> dict:
    """Abort a PlanRun.

    Returns a summary dict::

        {
            "plan_run_id": int,
            "status": "FAILED",
            "aborted_jobs": [int, ...],
            "released_leases": int,
            "phase": "precheck" | "running",
        }

    Raises :class:`PlanRunAbortError` if the PlanRun is already in a
    terminal status.
    """
    # Why: abort 也会写 pr.status,与 aggregator 并发时若不持锁会出现
    #      "aggregator 先 commit SUCCESS → abort 用 stale RUNNING 视图绕过
    #      aggregation guard,把状态改回 FAILED" 的覆盖。锁与 aggregator 同列。
    #      FOR NO KEY UPDATE 与 FK 触发的 FOR KEY SHARE 兼容,避免与 complete_job 的
    #      job UPDATE autoflush 死锁(见 aggregator.py 详细注释)。
    pr = db.execute(
        select(PlanRun)
        .where(PlanRun.id == plan_run_id)
        .with_for_update(key_share=True)
    ).scalar_one_or_none()
    if pr is None:
        raise PlanRunAbortError(f"PlanRun {plan_run_id} not found")

    if pr.status in _TERMINAL_PLAN_RUN_STATUSES:
        raise PlanRunAbortError(
            f"PlanRun {plan_run_id} is already terminal: {pr.status}"
        )

    run_ctx = dict(pr.run_context or {})
    precheck = run_ctx.get("precheck") or None

    # Detect "still in dispatch gate, no jobs yet".
    in_precheck = (
        precheck is not None
        and precheck.get("phase") in ("verifying", "syncing", "reverifying")
    )

    aborted_jobs: list[int] = []
    abort_requested_jobs: list[int] = []
    abort_jobs_by_host: dict[str, list[int]] = defaultdict(list)
    abort_hosts: set[str] = set()
    released_leases = 0

    if not in_precheck:
        all_jobs = (
            db.query(JobInstance)
            .filter(
                JobInstance.plan_run_id == plan_run_id,
            )
            .all()
        )
        active_jobs = [
            job for job in all_jobs
            if job.status in (JobStatus.PENDING.value, JobStatus.RUNNING.value)
        ]
        now = datetime.now(timezone.utc)
        for job in active_jobs:
            if job.status == JobStatus.PENDING.value:
                from backend.services.state_machine import JobStateMachine
                JobStateMachine.transition(job, JobStatus.ABORTED, reason)
                job.ended_at = now
                aborted_jobs.append(job.id)
            else:
                # RUNNING remains the authoritative state until the Agent has
                # killed the process tree and ACKed ABORTED via /complete.
                abort_requested_jobs.append(job.id)
                if job.host_id:
                    abort_hosts.add(job.host_id)
                    abort_jobs_by_host[job.host_id].append(job.id)
        # Mark abort_requested so reconciler / aggregator know this is intentional.
        run_ctx["abort_requested"] = {
            "at": now.isoformat(),
            "reason": reason,
            "triggered_by": triggered_by,
            "deadline_at": (
                now + timedelta(seconds=ABORT_ACK_GRACE_SECONDS)
            ).isoformat(),
            "requested_job_ids": abort_requested_jobs,
            "acknowledged_job_ids": [],
        }
        pr.run_context = run_ctx
        flag_modified(pr, "run_context")

        has_active_jobs = any(
            job.status in (JobStatus.PENDING.value, JobStatus.RUNNING.value)
            for job in all_jobs
        )
        if not has_active_jobs:
            if all_jobs:
                apply_plan_run_aggregation(pr, all_jobs)
            else:
                PlanRunStateMachine.transition(pr, PlanRunStatus.FAILED, reason=reason)
                pr.ended_at = now
                pr.result_summary = {
                    "aborted": True,
                    "reason": reason,
                    "empty_run": True,
                }
    # In-precheck path: no jobs to release; we close the PlanRun directly.
    now_iso = datetime.now(timezone.utc).isoformat()
    if in_precheck:
        precheck["phase"] = "failed"
        precheck["final_result"] = "aborted"
        precheck["completed_at"] = now_iso
        precheck.setdefault("errors", []).append(f"aborted: {reason}")
        run_ctx["precheck"] = precheck
        PlanRunStateMachine.transition(pr, PlanRunStatus.FAILED, reason=reason)
        pr.ended_at = datetime.now(timezone.utc)
        pr.result_summary = {
            "precheck_failed": True,
            "reason": reason,
            "aborted": True,
        }

    pr.run_context = run_ctx
    flag_modified(pr, "run_context")
    db.flush()

    record_audit(
        db,
        action=audit_action,
        resource_type="plan_run",
        resource_id=plan_run_id,
        details={
            "reason": reason,
            "phase": "precheck" if in_precheck else "running",
            "aborted_jobs": aborted_jobs,
            "abort_requested_jobs": abort_requested_jobs,
            "released_leases": released_leases,
            "triggered_by": triggered_by,
        },
        user_id=audit_user_id,
        username=audit_username,
    )
    db.commit()

    # Non-blocking control delivery.  The lease remains ACTIVE until the Agent
    # acknowledges termination, so a lost command cannot make the device
    # schedulable while the old process is still running.
    for host_id in abort_hosts:
        host_job_ids = abort_jobs_by_host.get(host_id, [])
        if not host_job_ids:
            continue
        schedule_emit(
            "control",
            {
                "command": "abort",
                "payload": {
                    "plan_run_id": plan_run_id,
                    "job_ids": host_job_ids,
                    "reason": reason,
                },
            },
            namespace="/agent",
            room=f"agent:{host_id}",
        )

    # ADR-0025 Sprint 4: abort 导致 PlanRun 终态时触发归档-2 scan + merge
    if should_trigger_dedup(pr.status):
        enqueue_dedup_terminal_sync(plan_run_id)

    # ── SocketIO push — align with _emit_job_status_invalidation pattern ──
    ts = datetime.now(timezone.utc).isoformat()
    room = f"plan_run:{plan_run_id}"
    for jid in aborted_jobs:
        schedule_emit(
            "job_status",
            {
                "type": "JOB_STATUS",
                "payload": {
                    "job_id": jid,
                    "plan_run_id": plan_run_id,
                    "status": "ABORTED",
                    "reason": reason,
                },
                "timestamp": ts,
            },
            namespace="/dashboard",
            room=room,
        )
    for jid in abort_requested_jobs:
        schedule_emit(
            "job_status",
            {
                "type": "JOB_STATUS",
                "payload": {
                    "job_id": jid,
                    "plan_run_id": plan_run_id,
                    "status": "RUNNING",
                    "abort_requested": True,
                    "reason": reason,
                },
                "timestamp": ts,
            },
            namespace="/dashboard",
            room=room,
        )
    schedule_emit(
        "plan_run_status",
        {
            "type": "PLAN_RUN_STATUS",
            "payload": {
                "plan_run_id": plan_run_id,
                "status": pr.status,
            },
            "timestamp": ts,
        },
        namespace="/dashboard",
        room=room,
    )

    db.refresh(pr)

    logger.info(
        "plan_run_aborted plan_run=%d phase=%s aborted_jobs=%d released_leases=%d",
        plan_run_id,
        "precheck" if in_precheck else "running",
        len(aborted_jobs),
        released_leases,
    )

    return {
        "plan_run_id": plan_run_id,
        "status": pr.status,
        "phase": "precheck" if in_precheck else "running",
        "aborted_jobs": aborted_jobs,
        "abort_requested_jobs": abort_requested_jobs,
        "released_leases": released_leases,
    }


def abort_jobs_for_host(
    host_id: str,
    *,
    db: Session,
    reason: str = "aborted_for_host_update",
    triggered_by: Optional[str] = None,
    audit_user_id: Optional[int] = None,
    audit_username: Optional[str] = None,
) -> dict:
    """Abort every active Job (PENDING/RUNNING) currently bound to ``host_id``.

    Walks the affected PlanRuns once each (deduped) and calls
    :func:`abort_plan_run` for them with ``audit_action='abort_jobs_for_host_update'``.

    Returns aggregate counts across PlanRuns::

        {
            "host_id": "...",
            "plan_runs": [int, ...],
            "aborted_jobs": [int, ...],
            "released_leases": int,
        }
    """
    active_jobs = (
        db.query(JobInstance.plan_run_id)
        .filter(
            JobInstance.host_id == host_id,
            JobInstance.status.in_(
                [JobStatus.PENDING.value, JobStatus.RUNNING.value]
            ),
        )
        .distinct()
        .all()
    )
    plan_run_ids = sorted({row[0] for row in active_jobs if row[0] is not None})

    aggregate_aborted: list[int] = []
    aggregate_released = 0
    for prid in plan_run_ids:
        try:
            summary = abort_plan_run(
                prid,
                db=db,
                reason=reason,
                triggered_by=triggered_by,
                audit_user_id=audit_user_id,
                audit_username=audit_username,
                audit_action="abort_jobs_for_host_update",
            )
        except PlanRunAbortError as exc:
            logger.warning(
                "abort_jobs_for_host_skip plan_run=%d host=%s: %s",
                prid, host_id, exc,
            )
            continue
        aggregate_aborted.extend(summary["aborted_jobs"])
        aggregate_released += summary["released_leases"]

    return {
        "host_id": host_id,
        "plan_runs": plan_run_ids,
        "aborted_jobs": aggregate_aborted,
        "released_leases": aggregate_released,
    }
