"""PlanRun API — ADR-0020.

Provides PlanRun list/detail/jobs/summary endpoints.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import get_current_active_user, User
from backend.core.audit import record_audit
from backend.core.database import get_db
from backend.models.enums import JobStatus
from backend.models.host import Device
from backend.models.job import JobArtifact, JobInstance, StepTrace
from backend.models.plan_run import PlanRun
from backend.services.plan_run_abort import (
    PlanRunAbortError,
    abort_plan_run,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["plan-runs"])


# ── Schemas ──────────────────────────────────────────────────────────────

class StepTraceOut(BaseModel):
    id: int
    job_id: int
    step_id: str
    stage: str
    event_type: str
    status: str
    output: Optional[str] = None
    error_message: Optional[str] = None
    original_ts: str
    created_at: str

    class Config:
        from_attributes = True


class JobInstanceOut(BaseModel):
    id: int
    plan_run_id: Optional[int] = None
    plan_id: Optional[int] = None
    device_id: int
    device_serial: Optional[str] = None
    host_id: Optional[str] = None
    status: str
    status_reason: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    created_at: Optional[str] = None
    step_traces: list[StepTraceOut] = []

    class Config:
        from_attributes = True


class PlanRunOut(BaseModel):
    id: int
    plan_id: int
    status: str
    failure_threshold: float
    run_type: str
    triggered_by: Optional[str] = None
    started_at: str
    ended_at: Optional[str] = None
    result_summary: Optional[dict] = None
    # ADR-0021: dispatch gate progress lives under run_context.precheck.
    run_context: Optional[dict] = None
    plan_snapshot: Optional[dict] = None
    parent_plan_run_id: Optional[int] = None
    root_plan_run_id: Optional[int] = None
    chain_index: int = 0
    next_plan_triggered: bool = False
    jobs: list[JobInstanceOut] = []

    class Config:
        from_attributes = True


# ── Helpers ──────────────────────────────────────────────────────────────

def _iso(v) -> str | None:
    if v is None:
        return None
    return v.isoformat()


def _plan_run_out(pr: PlanRun, jobs: list[JobInstanceOut] | None = None) -> PlanRunOut:
    return PlanRunOut(
        id=pr.id,
        plan_id=pr.plan_id,
        status=pr.status,
        failure_threshold=pr.failure_threshold,
        run_type=pr.run_type,
        triggered_by=pr.triggered_by,
        started_at=_iso(pr.started_at) or "",
        ended_at=_iso(pr.ended_at),
        result_summary=pr.result_summary,
        run_context=pr.run_context,
        plan_snapshot=pr.plan_snapshot,
        parent_plan_run_id=pr.parent_plan_run_id,
        root_plan_run_id=pr.root_plan_run_id,
        chain_index=pr.chain_index or 0,
        next_plan_triggered=bool(pr.next_plan_triggered),
        jobs=jobs or [],
    )


def _step_out(t: StepTrace) -> StepTraceOut:
    return StepTraceOut(
        id=t.id, job_id=t.job_id, step_id=t.step_id, stage=t.stage,
        event_type=t.event_type, status=t.status, output=t.output,
        error_message=t.error_message,
        original_ts=_iso(t.original_ts) or "",
        created_at=_iso(t.created_at) or "",
    )


def _job_out(job: JobInstance, traces: list, device_serial: str | None = None) -> JobInstanceOut:
    return JobInstanceOut(
        id=job.id, plan_run_id=job.plan_run_id, plan_id=job.plan_id,
        device_id=job.device_id, device_serial=device_serial,
        host_id=job.host_id, status=job.status,
        status_reason=job.status_reason,
        started_at=_iso(job.started_at),
        ended_at=_iso(job.ended_at),
        created_at=_iso(job.created_at),
        step_traces=[_step_out(t) for t in traces],
    )


# ── Endpoints ────────────────────────────────────────────────────────────

@router.get("/plan-runs", response_model=ApiResponse[list[PlanRunOut]])
def list_plan_runs(
    skip: int = 0,
    limit: int = 50,
    plan_id: Optional[int] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = select(PlanRun).order_by(PlanRun.started_at.desc())
    if plan_id is not None:
        q = q.where(PlanRun.plan_id == plan_id)
    if status is not None:
        q = q.where(PlanRun.status == status.upper())
    runs = db.execute(q.offset(skip).limit(limit)).scalars().all()
    return ok([_plan_run_out(r) for r in runs])


@router.get("/plan-runs/{run_id}", response_model=ApiResponse[PlanRunOut])
def get_plan_run(run_id: int, db: Session = Depends(get_db)):
    pr = db.get(PlanRun, run_id)
    if pr is None:
        raise HTTPException(status_code=404, detail="plan run not found")
    jobs = db.execute(
        select(JobInstance).where(JobInstance.plan_run_id == run_id)
    ).scalars().all()
    return ok(_plan_run_out(pr, jobs=[_job_out(j, []) for j in jobs]))


@router.get("/plan-runs/{run_id}/jobs", response_model=ApiResponse[list[JobInstanceOut]])
def list_plan_run_jobs(run_id: int, db: Session = Depends(get_db)):
    jobs = db.execute(
        select(JobInstance).where(JobInstance.plan_run_id == run_id)
    ).scalars().all()
    if not jobs:
        return ok([])

    device_ids = list({j.device_id for j in jobs})
    devices: dict[int, str] = {}
    if device_ids:
        rows = db.execute(
            select(Device.id, Device.serial).where(Device.id.in_(device_ids))
        ).all()
        devices = {r.id: r.serial for r in rows}

    job_ids = [j.id for j in jobs]
    all_traces = db.execute(
        select(StepTrace)
        .where(StepTrace.job_id.in_(job_ids))
        .order_by(StepTrace.original_ts)
    ).scalars().all()
    traces_by_job: dict[int, list] = {}
    for t in all_traces:
        traces_by_job.setdefault(t.job_id, []).append(t)

    return ok([
        _job_out(j, traces_by_job.get(j.id, []), devices.get(j.device_id))
        for j in jobs
    ])


# ── Abort ────────────────────────────────────────────────────────────────


class PlanRunAbortIn(BaseModel):
    reason: Optional[str] = None


@router.post(
    "/plan-runs/{run_id}/abort", response_model=ApiResponse[dict]
)
def abort_plan_run_endpoint(
    run_id: int,
    payload: Optional[PlanRunAbortIn] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """ADR-0021 D7 — abort a PlanRun.

    Returns 409 if the PlanRun is already terminal.  Otherwise releases
    active leases / marks PENDING jobs ABORTED / closes the run, then
    returns immediately (Agent drain happens asynchronously).
    """
    reason = (payload.reason if payload else None) or "aborted_by_user"
    try:
        summary = abort_plan_run(
            run_id,
            db=db,
            reason=reason,
            triggered_by=current_user.username if current_user else "api",
            audit_user_id=current_user.id if current_user else None,
            audit_username=current_user.username if current_user else None,
        )
    except PlanRunAbortError as exc:
        msg = str(exc)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=409, detail=msg)
    return ok(summary)


# ── ADR-0022: Manual retry / exit for patrol-backoff jobs ───────────────────


_NON_TERMINAL_JOB_STATUSES = {
    JobStatus.PENDING.value,
    JobStatus.RUNNING.value,
}


class JobManualActionIn(BaseModel):
    reason: Optional[str] = None


class JobManualActionOut(BaseModel):
    job_id: int
    plan_run_id: int
    action: str          # 'manual_retry' | 'manual_exit'
    status: str          # job status after the action
    manual_action: Optional[str] = None
    next_retry_at: Optional[str] = None
    current_failure_streak: int = 0


def _load_job_in_run(db: Session, run_id: int, job_id: int) -> JobInstance:
    job = db.get(JobInstance, job_id)
    if job is None or job.plan_run_id != run_id:
        raise HTTPException(status_code=404, detail="job not found in this plan run")
    return job


@router.post(
    "/plan-runs/{run_id}/jobs/{job_id}/manual-retry",
    response_model=ApiResponse[JobManualActionOut],
)
def manual_retry_job(
    run_id: int,
    job_id: int,
    payload: Optional[JobManualActionIn] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """ADR-0022 D7: clear backoff and force the next patrol cycle to run now.

    Sets ``next_retry_at = now()`` and ``manual_action = 'RETRY_NOW'`` so the
    Agent picks it up on the next heartbeat.  **Does not reset**
    ``current_failure_streak`` — diagnostic information is preserved.
    """
    job = _load_job_in_run(db, run_id, job_id)
    if job.status not in _NON_TERMINAL_JOB_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"job is in terminal status {job.status}; cannot retry",
        )

    reason = (payload.reason if payload else None) or "manual_retry"
    now = datetime.now(timezone.utc)

    job.next_retry_at = now
    job.manual_action = "RETRY_NOW"
    job.updated_at = now
    db.flush()

    record_audit(
        db,
        action="patrol_manual_retry",
        resource_type="job_instance",
        resource_id=job_id,
        details={
            "plan_run_id": run_id,
            "reason": reason,
            "current_failure_streak": job.current_failure_streak or 0,
            "triggered_by": current_user.username if current_user else None,
        },
        user_id=current_user.id if current_user else None,
        username=current_user.username if current_user else None,
    )
    db.commit()
    db.refresh(job)

    logger.info(
        "patrol_manual_retry plan_run=%d job=%d streak=%d",
        run_id, job_id, job.current_failure_streak or 0,
    )

    return ok(JobManualActionOut(
        job_id=job_id,
        plan_run_id=run_id,
        action="manual_retry",
        status=job.status,
        manual_action=job.manual_action,
        next_retry_at=_iso(job.next_retry_at),
        current_failure_streak=job.current_failure_streak or 0,
    ))


@router.post(
    "/plan-runs/{run_id}/jobs/{job_id}/manual-exit",
    response_model=ApiResponse[JobManualActionOut],
)
def manual_exit_job(
    run_id: int,
    job_id: int,
    payload: Optional[JobManualActionIn] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """ADR-0022 D7: request that the Agent skip the rest of patrol and abort.

    Sets ``manual_action = 'EXIT_REQUESTED'``.  The Agent observes this on the
    next heartbeat and exits the patrol loop **without running teardown** (BO4).
    Recycler / device lease release ensures the device returns to the pool.

    The job's status remains its current (PENDING/RUNNING) value here; it
    transitions to ABORTED once the Agent reports the terminal state via
    /jobs/{id}/complete (or via Recycler's stall detection).
    """
    job = _load_job_in_run(db, run_id, job_id)
    if job.status not in _NON_TERMINAL_JOB_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"job is in terminal status {job.status}; cannot exit",
        )

    reason = (payload.reason if payload else None) or "manual_exit"
    now = datetime.now(timezone.utc)

    job.manual_action = "EXIT_REQUESTED"
    if not job.status_reason:
        job.status_reason = f"patrol_manual_exit_pending: {reason}"
    job.updated_at = now
    db.flush()

    record_audit(
        db,
        action="patrol_manual_exit",
        resource_type="job_instance",
        resource_id=job_id,
        details={
            "plan_run_id": run_id,
            "reason": reason,
            "current_failure_streak": job.current_failure_streak or 0,
            "triggered_by": current_user.username if current_user else None,
        },
        user_id=current_user.id if current_user else None,
        username=current_user.username if current_user else None,
    )
    db.commit()
    db.refresh(job)

    logger.info(
        "patrol_manual_exit plan_run=%d job=%d streak=%d",
        run_id, job_id, job.current_failure_streak or 0,
    )

    return ok(JobManualActionOut(
        job_id=job_id,
        plan_run_id=run_id,
        action="manual_exit",
        status=job.status,
        manual_action=job.manual_action,
        next_retry_at=_iso(job.next_retry_at),
        current_failure_streak=job.current_failure_streak or 0,
    ))


@router.get("/plan-runs/{run_id}/summary", response_model=ApiResponse[dict])
def get_plan_run_summary(
    run_id: int,
    db: Session = Depends(get_db),
):
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

    return ok({
        "plan_run_id": run_id,
        "status": pr.status,
        "total_jobs": total,
        "status_counts": status_counts,
        "pass_rate": round(pass_rate, 4),
        "started_at": _iso(pr.started_at),
        "ended_at": _iso(pr.ended_at),
        "result_summary": pr.result_summary,
    })


# ── Artifacts ────────────────────────────────────────────────────────────

@router.get(
    "/plan-runs/{run_id}/jobs/{job_id}/artifacts",
    response_model=ApiResponse[list],
)
def list_job_artifacts(
    run_id: int,
    job_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    job = db.get(JobInstance, job_id)
    if job is None or job.plan_run_id != run_id:
        raise HTTPException(status_code=404, detail="job not found in this plan run")

    result = db.execute(
        select(JobArtifact).where(JobArtifact.job_id == job_id)
    )
    artifacts = result.scalars().all()
    return ok([
        {
            "id": a.id,
            "job_id": a.job_id,
            "filename": a.storage_uri.rsplit("/", 1)[-1] if a.storage_uri else None,
            "artifact_type": a.artifact_type,
            "size_bytes": a.size_bytes,
            "checksum": a.checksum,
            "created_at": _iso(a.created_at),
        }
        for a in artifacts
    ])


def _artifact_download_target(storage_uri: str) -> dict[str, str]:
    parsed = urlparse(storage_uri)
    scheme = parsed.scheme.lower()
    if scheme in {"http", "https"}:
        return {"kind": "redirect", "url": storage_uri}
    if scheme != "file":
        raise HTTPException(status_code=400, detail=f"unsupported artifact scheme: {scheme or 'empty'}")
    p = Path(("//" + parsed.netloc + unquote(parsed.path)) if parsed.netloc else unquote(parsed.path))
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail=f"artifact file not found: {p}")
    return {"kind": "local", "path": str(p)}


@router.get(
    "/plan-runs/{run_id}/jobs/{job_id}/artifacts/{artifact_id}/download",
)
def download_job_artifact(
    run_id: int,
    job_id: int,
    artifact_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    job = db.get(JobInstance, job_id)
    if job is None or job.plan_run_id != run_id:
        raise HTTPException(status_code=404, detail="job not found in this plan run")

    artifact = db.get(JobArtifact, artifact_id)
    if artifact is None or artifact.job_id != job_id:
        raise HTTPException(status_code=404, detail="artifact not found for this job")

    target = _artifact_download_target(artifact.storage_uri)
    if target["kind"] == "redirect":
        return RedirectResponse(url=target["url"], status_code=307)
    local_path = Path(target["path"])
    media_type = "application/gzip" if local_path.suffixes[-2:] == [".tar", ".gz"] else None
    return FileResponse(path=str(local_path), filename=local_path.name, media_type=media_type)
