"""Jobs query routes — bulk occupancy snapshot for plan-execute.

Independent ``/api/v1/jobs/...`` prefix avoids FastAPI capturing
``active-by-device`` under ``/api/v1/hosts/{host_id}``.
"""

from __future__ import annotations

from typing import Any, List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.api.routes.auth import User, get_current_active_user
from backend.api.schemas import HostActiveJob
from backend.core.database import get_db
from backend.core.legacy_aee import hidden_legacy_plan_ids
from backend.models.enums import JobStatus
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

_ACTIVE_JOB_STATUSES = (JobStatus.PENDING.value, JobStatus.RUNNING.value)


def _visible_plan_id(plan_id: int | None, hidden_plan_ids_set: set[int]) -> int | None:
    if plan_id is None:
        return None
    return None if plan_id in hidden_plan_ids_set else plan_id


@router.get("/active-by-device", response_model=List[HostActiveJob])
def list_active_jobs_by_device(
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """Return all PENDING/RUNNING jobs keyed for device occupancy UI.

    Shape matches ``HostActiveJob`` (same fields as ``GET /hosts/{id}.active_jobs``)
    so the plan-execute page can render PlanRun jump links without N+1 host detail
    fetches. Heartbeat-derived capacity remains on host list; this is occupancy only.
    """
    hidden_plan_ids_set = hidden_legacy_plan_ids(db)
    active_jobs = (
        db.query(JobInstance)
        .filter(JobInstance.status.in_(_ACTIVE_JOB_STATUSES))
        .order_by(JobInstance.id)
        .all()
    )
    pr_ids = {j.plan_run_id for j in active_jobs if j.plan_run_id is not None}
    pr_map: dict[int, Any] = {}
    if pr_ids:
        pr_rows = db.query(PlanRun).filter(PlanRun.id.in_(pr_ids)).all()
        pr_map = {pr.id: pr for pr in pr_rows}

    def _abort_pending(j: JobInstance) -> bool:
        pr = pr_map.get(j.plan_run_id) if j.plan_run_id is not None else None
        if pr is None or pr.run_context is None:
            return False
        return isinstance(pr.run_context, dict) and "abort_requested" in pr.run_context

    return [
        HostActiveJob(
            id=j.id,
            plan_run_id=j.plan_run_id,
            plan_id=_visible_plan_id(j.plan_id, hidden_plan_ids_set),
            device_id=j.device_id,
            status=j.status,
            started_at=j.started_at,
            abort_pending=_abort_pending(j),
        )
        for j in active_jobs
    ]
