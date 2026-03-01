"""Orchestration API: WorkflowDefinition CRUD + WorkflowRun trigger + queries."""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.response import ApiResponse, err, ok
from backend.core.database import get_async_db
from backend.models.job import JobInstance, StepTrace, TaskTemplate
from backend.models.host import Device
from backend.models.workflow import WorkflowDefinition, WorkflowRun
from backend.services.dispatcher import DispatchError, dispatch_workflow

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["orchestration"])


# ── Request/Response schemas ──────────────────────────────────────────────────

class TaskTemplateIn(BaseModel):
    name: str
    pipeline_def: dict
    platform_filter: Optional[dict] = None
    sort_order: int = 0


class WorkflowDefCreate(BaseModel):
    name: str
    description: Optional[str] = None
    failure_threshold: float = Field(default=0.05, ge=0.0, le=1.0)
    task_templates: List[TaskTemplateIn] = Field(default_factory=list)


class WorkflowDefUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    failure_threshold: Optional[float] = None
    task_templates: Optional[List[TaskTemplateIn]] = None


class WorkflowRunTrigger(BaseModel):
    device_ids: List[int]
    failure_threshold: Optional[float] = None


class TaskTemplateOut(BaseModel):
    id: int
    name: str
    pipeline_def: dict
    platform_filter: Optional[dict]
    sort_order: int
    created_at: datetime


class WorkflowDefOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    failure_threshold: float
    created_by: Optional[str]
    created_at: datetime
    updated_at: datetime
    task_templates: List[TaskTemplateOut] = []


class StepTraceOut(BaseModel):
    id: int
    job_id: int
    step_id: str
    stage: str
    event_type: str
    status: str
    output: Optional[str]
    error_message: Optional[str]
    original_ts: datetime
    created_at: datetime


class JobInstanceOut(BaseModel):
    id: int
    workflow_run_id: int
    task_template_id: int
    device_id: int
    device_serial: Optional[str] = None
    host_id: Optional[str]
    status: str
    status_reason: Optional[str]
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    created_at: datetime
    step_traces: List[StepTraceOut] = []


class WorkflowRunOut(BaseModel):
    id: int
    workflow_definition_id: int
    status: str
    failure_threshold: float
    triggered_by: Optional[str]
    started_at: datetime
    ended_at: Optional[datetime]
    result_summary: Optional[dict]
    jobs: List[JobInstanceOut] = []


# ── WorkflowDefinition CRUD ───────────────────────────────────────────────────

@router.post("/workflows", response_model=ApiResponse[WorkflowDefOut])
async def create_workflow(
    payload: WorkflowDefCreate,
    db: AsyncSession = Depends(get_async_db),
):
    now = datetime.utcnow()
    wf = WorkflowDefinition(
        name=payload.name,
        description=payload.description,
        failure_threshold=payload.failure_threshold,
        created_at=now,
        updated_at=now,
    )
    db.add(wf)
    await db.flush()

    templates = []
    for t in payload.task_templates:
        tmpl = TaskTemplate(
            workflow_definition_id=wf.id,
            name=t.name,
            pipeline_def=t.pipeline_def,
            platform_filter=t.platform_filter,
            sort_order=t.sort_order,
            created_at=now,
        )
        db.add(tmpl)
        templates.append(tmpl)

    await db.commit()
    await db.refresh(wf)
    return ok(_wf_out(wf, templates))


@router.get("/workflows", response_model=ApiResponse[List[WorkflowDefOut]])
async def list_workflows(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_async_db),
):
    rows = (await db.execute(
        select(WorkflowDefinition).order_by(WorkflowDefinition.created_at.desc())
        .offset(skip).limit(limit)
    )).scalars().all()
    result = []
    for wf in rows:
        templates = (await db.execute(
            select(TaskTemplate).where(TaskTemplate.workflow_definition_id == wf.id)
            .order_by(TaskTemplate.sort_order)
        )).scalars().all()
        result.append(_wf_out(wf, templates))
    return ok(result)


@router.get("/workflows/{wf_id}", response_model=ApiResponse[WorkflowDefOut])
async def get_workflow(wf_id: int, db: AsyncSession = Depends(get_async_db)):
    wf = await db.get(WorkflowDefinition, wf_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    templates = (await db.execute(
        select(TaskTemplate).where(TaskTemplate.workflow_definition_id == wf_id)
        .order_by(TaskTemplate.sort_order)
    )).scalars().all()
    return ok(_wf_out(wf, templates))


@router.put("/workflows/{wf_id}", response_model=ApiResponse[WorkflowDefOut])
async def update_workflow(
    wf_id: int,
    payload: WorkflowDefUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    wf = await db.get(WorkflowDefinition, wf_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    if payload.name is not None:
        wf.name = payload.name
    if payload.description is not None:
        wf.description = payload.description
    if payload.failure_threshold is not None:
        wf.failure_threshold = payload.failure_threshold
    wf.updated_at = datetime.utcnow()

    if payload.task_templates is not None:
        # Replace all templates: delete existing, insert new
        from sqlalchemy import delete as sa_delete
        await db.execute(sa_delete(TaskTemplate).where(TaskTemplate.workflow_definition_id == wf_id))
        now = datetime.utcnow()
        for t in payload.task_templates:
            db.add(TaskTemplate(
                workflow_definition_id=wf_id,
                name=t.name,
                pipeline_def=t.pipeline_def,
                platform_filter=t.platform_filter,
                sort_order=t.sort_order,
                created_at=now,
            ))

    await db.commit()
    await db.refresh(wf)
    templates = (await db.execute(
        select(TaskTemplate).where(TaskTemplate.workflow_definition_id == wf_id)
        .order_by(TaskTemplate.sort_order)
    )).scalars().all()
    return ok(_wf_out(wf, templates))


@router.delete("/workflows/{wf_id}", response_model=ApiResponse[dict])
async def delete_workflow(wf_id: int, db: AsyncSession = Depends(get_async_db)):
    wf = await db.get(WorkflowDefinition, wf_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    active_runs = (await db.execute(
        select(WorkflowRun).where(
            WorkflowRun.workflow_definition_id == wf_id,
            WorkflowRun.status == "RUNNING",
        ).limit(1)
    )).scalars().first()
    if active_runs:
        raise HTTPException(status_code=409, detail="cannot delete workflow with active runs")
    await db.delete(wf)
    await db.commit()
    return ok({"deleted": wf_id})


# ── Dispatch ──────────────────────────────────────────────────────────────────

@router.post("/workflows/{wf_id}/run", response_model=ApiResponse[WorkflowRunOut])
async def run_workflow(
    wf_id: int,
    payload: WorkflowRunTrigger,
    db: AsyncSession = Depends(get_async_db),
):
    wf_def = await db.get(WorkflowDefinition, wf_id)
    threshold = payload.failure_threshold if payload.failure_threshold is not None \
        else (wf_def.failure_threshold if wf_def else 0.05)
    try:
        run = await dispatch_workflow(
            workflow_def_id=wf_id,
            device_ids=payload.device_ids,
            failure_threshold=threshold,
            triggered_by="api",
            db=db,
        )
    except DispatchError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ok(_run_out(run, []))


# ── WorkflowRun queries ────────────────────────────────────────────────────────

@router.get("/workflow-runs", response_model=ApiResponse[List[WorkflowRunOut]])
async def list_workflow_runs(
    skip: int = 0, limit: int = 50,
    db: AsyncSession = Depends(get_async_db),
):
    runs = (await db.execute(
        select(WorkflowRun).order_by(WorkflowRun.started_at.desc()).offset(skip).limit(limit)
    )).scalars().all()
    return ok([_run_out(r, []) for r in runs])


@router.get("/workflow-runs/{run_id}", response_model=ApiResponse[WorkflowRunOut])
async def get_workflow_run(run_id: int, db: AsyncSession = Depends(get_async_db)):
    run = await db.get(WorkflowRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="workflow run not found")
    jobs = (await db.execute(
        select(JobInstance).where(JobInstance.workflow_run_id == run_id)
    )).scalars().all()
    return ok(_run_out(run, jobs))


@router.get("/workflow-runs/{run_id}/jobs", response_model=ApiResponse[List[JobInstanceOut]])
async def list_run_jobs(run_id: int, db: AsyncSession = Depends(get_async_db)):
    jobs = (await db.execute(
        select(JobInstance).where(JobInstance.workflow_run_id == run_id)
    )).scalars().all()

    # Batch-fetch device serials
    device_ids = list({j.device_id for j in jobs})
    devices: dict[int, str] = {}
    if device_ids:
        rows = (await db.execute(
            select(Device.id, Device.serial).where(Device.id.in_(device_ids))
        )).all()
        devices = {r.id: r.serial for r in rows}

    result = []
    for job in jobs:
        traces = (await db.execute(
            select(StepTrace).where(StepTrace.job_id == job.id)
            .order_by(StepTrace.original_ts)
        )).scalars().all()
        result.append(_job_out(job, traces, devices.get(job.device_id)))
    return ok(result)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _wf_out(wf: WorkflowDefinition, templates: list) -> WorkflowDefOut:
    return WorkflowDefOut(
        id=wf.id, name=wf.name, description=wf.description,
        failure_threshold=wf.failure_threshold, created_by=wf.created_by,
        created_at=wf.created_at, updated_at=wf.updated_at,
        task_templates=[
            TaskTemplateOut(
                id=t.id, name=t.name, pipeline_def=t.pipeline_def,
                platform_filter=t.platform_filter, sort_order=t.sort_order,
                created_at=t.created_at,
            )
            for t in templates
        ],
    )


def _run_out(run: WorkflowRun, jobs: list) -> WorkflowRunOut:
    return WorkflowRunOut(
        id=run.id, workflow_definition_id=run.workflow_definition_id,
        status=run.status, failure_threshold=run.failure_threshold,
        triggered_by=run.triggered_by, started_at=run.started_at,
        ended_at=run.ended_at, result_summary=run.result_summary,
        jobs=[_job_out(j, []) for j in jobs],
    )


def _job_out(job: JobInstance, traces: list, device_serial: Optional[str] = None) -> JobInstanceOut:
    return JobInstanceOut(
        id=job.id, workflow_run_id=job.workflow_run_id,
        task_template_id=job.task_template_id, device_id=job.device_id,
        device_serial=device_serial, host_id=job.host_id,
        status=job.status, status_reason=job.status_reason,
        started_at=job.started_at, ended_at=job.ended_at, created_at=job.created_at,
        step_traces=[
            StepTraceOut(
                id=t.id, job_id=t.job_id, step_id=t.step_id, stage=t.stage,
                event_type=t.event_type, status=t.status, output=t.output,
                error_message=t.error_message, original_ts=t.original_ts,
                created_at=t.created_at,
            )
            for t in traces
        ],
    )
