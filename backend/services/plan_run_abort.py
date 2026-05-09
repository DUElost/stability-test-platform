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
  - RUNNING jobs → release ACTIVE device leases → Agent's LeaseRenewer
    detects 409 → pipeline_engine drains current step → reports ABORTED
  - reconciler is the safety net for unresponsive agents (15s tick)
- already terminal: no-op, returns False

Always writes audit_log(action='abort_plan_run').

The function is **non-blocking**: it does not wait for agents to respond.
Frontend re-renders via SocketIO room ``plan_run:{id}``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from backend.realtime.socketio_server import schedule_emit
from typing import Optional

from sqlalchemy import update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.core.audit import record_audit
from backend.models.device_lease import DeviceLease
from backend.models.enums import JobStatus, LeaseStatus, LeaseType
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun

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


def _release_active_lease_sync(
    db: Session, device_id: int, job_id: int
) -> bool:
    """Release the ACTIVE JOB lease for (device_id, job_id) if present.

    Mirrors :func:`backend.services.lease_manager.release_lease_sync` but
    inlined here to avoid pulling the async-flavoured module's ORM imports
    into this sync hot path.
    """
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(DeviceLease)
        .where(
            DeviceLease.device_id == device_id,
            DeviceLease.job_id == job_id,
            DeviceLease.lease_type == LeaseType.JOB.value,
            DeviceLease.status == LeaseStatus.ACTIVE.value,
        )
        .values(status=LeaseStatus.RELEASED.value, released_at=now)
    )
    return bool(result.rowcount)


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
    pr = db.get(PlanRun, plan_run_id)
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
        and precheck.get("phase") in ("verifying", "syncing")
    )

    aborted_jobs: list[int] = []
    released_leases = 0

    if not in_precheck:
        active_jobs = (
            db.query(JobInstance)
            .filter(
                JobInstance.plan_run_id == plan_run_id,
                JobInstance.status.in_(
                    [JobStatus.PENDING.value, JobStatus.RUNNING.value]
                ),
            )
            .all()
        )
        now = datetime.now(timezone.utc)
        for job in active_jobs:
            if job.status == JobStatus.PENDING.value:
                job.status = JobStatus.ABORTED.value
                job.status_reason = reason
                job.ended_at = now
                aborted_jobs.append(job.id)
            else:
                # RUNNING: release lease, let Agent drain naturally.
                if _release_active_lease_sync(db, job.device_id, job.id):
                    released_leases += 1
                aborted_jobs.append(job.id)
        # Mark abort_requested so reconciler / aggregator know this is intentional.
        run_ctx["abort_requested"] = {
            "at": now.isoformat() + "Z",
            "reason": reason,
            "triggered_by": triggered_by,
        }

    # In-precheck path: no jobs to release; we close the PlanRun directly.
    now_iso = datetime.now(timezone.utc).isoformat() + "Z"
    if in_precheck:
        precheck["phase"] = "failed"
        precheck["final_result"] = "aborted"
        precheck["completed_at"] = now_iso
        precheck.setdefault("errors", []).append(f"aborted: {reason}")
        run_ctx["precheck"] = precheck
        pr.status = "FAILED"
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
            "released_leases": released_leases,
            "triggered_by": triggered_by,
        },
        user_id=audit_user_id,
        username=audit_username,
    )
    db.commit()

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
