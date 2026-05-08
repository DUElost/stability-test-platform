from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from backend.core.metrics import record_plan_run_terminal
from backend.models.enums import JobStatus, PlanRunStatus

_TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.ABORTED, JobStatus.UNKNOWN}


def apply_plan_run_aggregation(run: Any, jobs: Sequence[Any]) -> bool:
    """Apply the shared PlanRun terminal aggregation rule."""
    if not all(JobStatus(j.status) in _TERMINAL for j in jobs):
        return False

    total = len(jobs)
    if total == 0:
        run.status = PlanRunStatus.FAILED.value
        run.ended_at = datetime.now(timezone.utc)
        record_plan_run_terminal(run.status, pass_rate=0.0)
        return True

    failed = sum(1 for j in jobs if JobStatus(j.status) in {JobStatus.FAILED, JobStatus.ABORTED})
    unknown = sum(1 for j in jobs if JobStatus(j.status) == JobStatus.UNKNOWN)

    if unknown > 0:
        run.status = PlanRunStatus.DEGRADED.value
    elif failed == 0:
        run.status = PlanRunStatus.SUCCESS.value
    elif failed / total <= run.failure_threshold:
        run.status = PlanRunStatus.PARTIAL_SUCCESS.value
    else:
        run.status = PlanRunStatus.FAILED.value

    run.ended_at = datetime.now(timezone.utc)

    completed = sum(1 for j in jobs if JobStatus(j.status) == JobStatus.COMPLETED)
    pass_rate = round(completed / total, 4) if total else 0
    run.result_summary = {
        "total": total,
        "completed": completed,
        "failed": failed,
        "unknown": unknown,
        "pass_rate": pass_rate,
    }
    record_plan_run_terminal(run.status, pass_rate=float(pass_rate))
    return True
