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

    failed_only = sum(1 for j in jobs if JobStatus(j.status) == JobStatus.FAILED)
    aborted = sum(1 for j in jobs if JobStatus(j.status) == JobStatus.ABORTED)
    unknown = sum(1 for j in jobs if JobStatus(j.status) == JobStatus.UNKNOWN)

    # v3: abort 是有意终止信号, 不参与 failure_threshold 容忍判断;
    #     任何 ABORTED → FAILED (覆盖 threshold).
    if unknown > 0:
        run.status = PlanRunStatus.DEGRADED.value
    elif failed_only + aborted == 0:
        run.status = PlanRunStatus.SUCCESS.value
    elif aborted > 0:
        run.status = PlanRunStatus.FAILED.value
    elif failed_only / total <= run.failure_threshold:
        run.status = PlanRunStatus.PARTIAL_SUCCESS.value
    else:
        run.status = PlanRunStatus.FAILED.value

    run.ended_at = datetime.now(timezone.utc)

    completed = sum(1 for j in jobs if JobStatus(j.status) == JobStatus.COMPLETED)
    pass_rate = round(completed / total, 4) if total else 0
    run.result_summary = {
        "total": total,
        "completed": completed,
        "failed": failed_only + aborted,   # 兼容字段: terminal failure 总数
        "failed_only": failed_only,        # v3: 自然失败 (不含 aborted)
        "aborted": aborted,                # v3: abort 终止数
        "unknown": unknown,
        "pass_rate": pass_rate,
    }
    record_plan_run_terminal(run.status, pass_rate=float(pass_rate))
    return True
