"""Script execution facade API."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from backend.api.response import ApiResponse, ok
from backend.core.database import get_db
from backend.models.job import JobInstance
from backend.models.workflow import WorkflowRun
from backend.services.script_execution import (
    create_script_execution,
    resolve_execution_items,
    script_execution_detail,
)

router = APIRouter(prefix="/api/v1/script-executions", tags=["script-executions"])

DEVICE_SERIAL_PREVIEW_LIMIT = 3


class ScriptExecutionCreate(BaseModel):
    sequence_id: Optional[int] = None
    items: List[Dict[str, Any]] = Field(default_factory=list)
    device_ids: List[int] = Field(default_factory=list)
    on_failure: str = "stop"


class ScriptExecutionCreated(BaseModel):
    workflow_run_id: int
    job_ids: List[int]
    device_count: int
    step_count: int


@router.post("", response_model=ApiResponse[ScriptExecutionCreated], status_code=201)
def create_execution(payload: ScriptExecutionCreate, db: Session = Depends(get_db)):
    items, sequence_id = resolve_execution_items(
        db,
        sequence_id=payload.sequence_id,
        items=payload.items,
    )
    created = create_script_execution(
        db,
        items=items,
        device_ids=payload.device_ids,
        sequence_id=sequence_id,
        on_failure=payload.on_failure,
    )
    return ok(ScriptExecutionCreated(**created))


@router.get("", response_model=ApiResponse[dict])
def list_executions(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    query = (
        db.query(WorkflowRun)
        .filter(
            WorkflowRun.result_summary.isnot(None),
            WorkflowRun.result_summary["mode"].astext == "script_execution",
        )
        .order_by(WorkflowRun.started_at.desc(), WorkflowRun.id.desc())
    )
    total = query.count()
    rows = query.offset(skip).limit(limit).all()

    run_ids = [row.id for row in rows]
    jobs_map: dict[int, list[JobInstance]] = {}
    if run_ids:
        jobs = (
            db.query(JobInstance)
            .options(joinedload(JobInstance.device), joinedload(JobInstance.host))
            .filter(JobInstance.workflow_run_id.in_(run_ids))
            .order_by(JobInstance.id)
            .all()
        )
        for job in jobs:
            jobs_map.setdefault(job.workflow_run_id, []).append(job)

    items = []
    for row in rows:
        row_jobs = jobs_map.get(row.id, [])
        device_count = len(row_jobs)
        device_serials = [
            job.device.serial
            for job in row_jobs[:DEVICE_SERIAL_PREVIEW_LIMIT]
            if job.device is not None
        ]
        result_items = (row.result_summary or {}).get("items") or []
        script_names = " → ".join(
            item["script_name"] for item in result_items if item.get("script_name")
        )
        host_name = None
        if row_jobs:
            first_job = row_jobs[0]
            if first_job.host:
                host_name = first_job.host.name or first_job.host.hostname

        items.append(
            {
                "workflow_run_id": row.id,
                "status": row.status,
                "started_at": row.started_at,
                "ended_at": row.ended_at,
                "sequence_id": (row.result_summary or {}).get("sequence_id"),
                "step_count": len(result_items),
                "device_count": device_count,
                "device_serials": device_serials,
                "script_names": script_names,
                "host_name": host_name,
            }
        )

    return ok(
        {
            "items": items,
            "total": total,
            "skip": skip,
            "limit": limit,
        }
    )


@router.get("/{run_id}", response_model=ApiResponse[dict])
def get_execution(run_id: int, db: Session = Depends(get_db)):
    return ok(script_execution_detail(db, run_id))


@router.post("/{run_id}/rerun", response_model=ApiResponse[ScriptExecutionCreated], status_code=201)
def rerun_execution(run_id: int, db: Session = Depends(get_db)):
    detail = script_execution_detail(db, run_id)
    device_ids = [job["device_id"] for job in detail["jobs"]]
    created = create_script_execution(
        db,
        items=detail["items"],
        device_ids=device_ids,
        sequence_id=detail.get("sequence_id"),
        on_failure=detail.get("on_failure", "stop"),
    )
    return ok(ScriptExecutionCreated(**created))
