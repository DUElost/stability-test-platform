from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from backend.core.metrics import record_plan_run_terminal
from backend.models.enums import JobStatus, PlanRunStatus
from backend.services.state_machine import PlanRunStateMachine

_TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.ABORTED}
# UNKNOWN is intentionally excluded: with the B4 fix (agent_api.py _TERMINAL no
# longer contains UNKNOWN), an UNKNOWN job may still transition to COMPLETED/
# FAILED via Agent completion.  PlanRun aggregation must wait until every job
# reaches a truly final state; the reconciler will eventually convert UNKNOWN→
# FAILED when the grace window expires.

_TERMINAL_PLAN_RUN_STATUSES = {
    PlanRunStatus.SUCCESS.value,
    PlanRunStatus.PARTIAL_SUCCESS.value,
    PlanRunStatus.FAILED.value,
    PlanRunStatus.DEGRADED.value,
}


def _resolve_plan_run_status(
    *,
    total: int,
    failed_only: int,
    aborted: int,
    failure_threshold: float,
    abort_requested: bool,
) -> PlanRunStatus:
    if failed_only + aborted == 0:
        new_status = PlanRunStatus.SUCCESS
    elif aborted > 0:
        new_status = PlanRunStatus.FAILED
    elif failed_only / total <= failure_threshold:
        new_status = PlanRunStatus.PARTIAL_SUCCESS
    else:
        new_status = PlanRunStatus.FAILED

    if abort_requested and new_status in (
        PlanRunStatus.SUCCESS,
        PlanRunStatus.PARTIAL_SUCCESS,
    ):
        new_status = PlanRunStatus.FAILED
    return new_status


def _abort_requested(run: Any) -> bool:
    run_context = getattr(run, "run_context", None)
    return isinstance(run_context, dict) and "abort_requested" in run_context


def _finalize_plan_run(
    run: Any,
    *,
    new_status: PlanRunStatus,
    total: int,
    completed: int,
    failed_only: int,
    aborted: int,
    abort_requested: bool,
) -> bool:
    PlanRunStateMachine.transition(run, new_status, reason="aggregation")
    run.ended_at = datetime.now(timezone.utc)
    pass_rate = round(completed / total, 4) if total else 0
    run.result_summary = {
        "total": total,
        "completed": completed,
        "failed": failed_only + aborted,
        "failed_only": failed_only,
        "aborted": aborted,
        "unknown": 0,
        "pass_rate": pass_rate,
        "abort_requested": abort_requested,
    }
    record_plan_run_terminal(run.status, pass_rate=float(pass_rate))
    return True


def apply_plan_run_aggregation_from_counters(run: Any) -> bool:
    """O(1) aggregation from plan_run counters (ADR-0026 §6).

    Requires ``total_job_count > 0`` and ``terminal_job_count >= total_job_count``.
    """
    if run.status in _TERMINAL_PLAN_RUN_STATUSES:
        return False
    total = int(getattr(run, "total_job_count", 0) or 0)
    terminal = int(getattr(run, "terminal_job_count", 0) or 0)
    if total <= 0 or terminal < total:
        return False

    failed_only = int(getattr(run, "failed_job_count", 0) or 0)
    aborted = int(getattr(run, "aborted_job_count", 0) or 0)
    completed = int(getattr(run, "completed_job_count", 0) or 0)
    abort_requested = _abort_requested(run)
    new_status = _resolve_plan_run_status(
        total=total,
        failed_only=failed_only,
        aborted=aborted,
        failure_threshold=float(run.failure_threshold),
        abort_requested=abort_requested,
    )
    return _finalize_plan_run(
        run,
        new_status=new_status,
        total=total,
        completed=completed,
        failed_only=failed_only,
        aborted=aborted,
        abort_requested=abort_requested,
    )


def apply_plan_run_aggregation(run: Any, jobs: Sequence[Any]) -> bool:
    """Apply the shared PlanRun terminal aggregation rule (full job scan)."""
    # Why: aggregator(async/sync) + abort 三处都会落终态,无守卫时第二个写入会覆盖第一个
    #      (例如 abort 后 aggregator 又把 ABORTED 改回 SUCCESS),配合上游 SELECT ... FOR UPDATE
    #      保证 read-modify-write 串行化。
    if run.status in _TERMINAL_PLAN_RUN_STATUSES:
        return False
    if not all(JobStatus(j.status) in _TERMINAL for j in jobs):
        return False

    total = len(jobs)
    if total == 0:
        PlanRunStateMachine.transition(run, PlanRunStatus.FAILED, reason="empty_job_set")
        run.ended_at = datetime.now(timezone.utc)
        record_plan_run_terminal(run.status, pass_rate=0.0)
        return True

    failed_only = sum(1 for j in jobs if JobStatus(j.status) == JobStatus.FAILED)
    aborted = sum(1 for j in jobs if JobStatus(j.status) == JobStatus.ABORTED)
    completed = sum(1 for j in jobs if JobStatus(j.status) == JobStatus.COMPLETED)
    abort_requested = _abort_requested(run)

    new_status = _resolve_plan_run_status(
        total=total,
        failed_only=failed_only,
        aborted=aborted,
        failure_threshold=float(run.failure_threshold),
        abort_requested=abort_requested,
    )
    return _finalize_plan_run(
        run,
        new_status=new_status,
        total=total,
        completed=completed,
        failed_only=failed_only,
        aborted=aborted,
        abort_requested=abort_requested,
    )
