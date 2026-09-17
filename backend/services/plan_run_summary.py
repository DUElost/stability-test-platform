"""PlanRun 作业状态摘要（#1520 垂直切片：GET /plan-runs/{id}/summary）。

路由退化为 ``ok(build_plan_run_summary(...))``。
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.api.schemas.plan_run import PlanRunJobsSummaryOut
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun
from backend.services.plan_run_read_common import iso


def build_plan_run_summary(db: Session, run_id: int) -> PlanRunJobsSummaryOut:
    pr = db.get(PlanRun, run_id)
    if pr is None:
        raise HTTPException(status_code=404, detail="plan run not found")

    jobs_result = db.execute(
        select(
            JobInstance.status,
            func.count(JobInstance.id),
        )
        .where(JobInstance.plan_run_id == run_id)
        .group_by(JobInstance.status)
    )
    status_counts = {row[0]: row[1] for row in jobs_result.all()}
    total = sum(status_counts.values())
    pass_rate = (
        status_counts.get("COMPLETED", 0) / total if total > 0 else 0.0
    )

    return PlanRunJobsSummaryOut(
        plan_run_id=run_id,
        status=pr.status,
        total_jobs=total,
        status_counts=status_counts,
        pass_rate=round(pass_rate, 4),
        started_at=iso(pr.started_at),
        ended_at=iso(pr.ended_at),
        result_summary=pr.result_summary,
    )
