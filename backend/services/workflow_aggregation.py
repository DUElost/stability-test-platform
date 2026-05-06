from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from backend.models.enums import JobStatus, WorkflowStatus

_TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.ABORTED, JobStatus.UNKNOWN}


def apply_workflow_aggregation(run: Any, jobs: Sequence[Any]) -> bool:
    """Apply the shared WorkflowRun terminal aggregation rule."""
    if not all(JobStatus(j.status) in _TERMINAL for j in jobs):
        return False

    total = len(jobs)
    if total == 0:
        run.status = WorkflowStatus.FAILED.value
        run.ended_at = datetime.now(timezone.utc)
        return True

    failed = sum(1 for j in jobs if JobStatus(j.status) in {JobStatus.FAILED, JobStatus.ABORTED})
    unknown = sum(1 for j in jobs if JobStatus(j.status) == JobStatus.UNKNOWN)

    if unknown > 0:
        run.status = WorkflowStatus.DEGRADED.value
    elif failed == 0:
        run.status = WorkflowStatus.SUCCESS.value
    elif failed / total <= run.failure_threshold:
        run.status = WorkflowStatus.PARTIAL_SUCCESS.value
    else:
        run.status = WorkflowStatus.FAILED.value

    run.ended_at = datetime.now(timezone.utc)

    completed = sum(1 for j in jobs if JobStatus(j.status) == JobStatus.COMPLETED)
    run.result_summary = {
        "total": total,
        "completed": completed,
        "failed": failed,
        "unknown": unknown,
        "pass_rate": round(completed / total, 4) if total else 0,
    }
    return True
