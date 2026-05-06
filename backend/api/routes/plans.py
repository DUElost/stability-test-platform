"""Plan API — ADR-0020.

Plan CRUD + run/run/preview endpoints.  Replaces the WorkflowDefinition endpoints
in ``backend/api/routes/orchestration.py``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import get_current_active_user, User
from backend.core.database import get_db
from backend.core.pipeline_validator import validate_pipeline_def
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.services.plan_dispatcher_sync import (
    PlanDispatchError,
    dispatch_plan_sync,
    preview_plan_dispatch_sync,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["plans"])


# ── Schemas ──────────────────────────────────────────────────────────────

class PlanStepIn(BaseModel):
    step_key: str
    script_name: str
    script_version: str
    stage: str = Field(..., pattern="^(init|patrol|teardown)$")
    sort_order: int = 0
    timeout_seconds: Optional[int] = None
    retry: int = Field(default=0, ge=0, le=5)


class PlanCreate(BaseModel):
    name: str
    description: Optional[str] = None
    failure_threshold: float = Field(default=0.05, ge=0.0, le=1.0)
    lifecycle: dict
    next_plan_id: Optional[int] = None
    watcher_policy: Optional[dict] = None
    steps: List[PlanStepIn] = Field(default_factory=list)


class PlanUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    failure_threshold: Optional[float] = None
    lifecycle: Optional[dict] = None
    next_plan_id: Optional[int] = None
    watcher_policy: Optional[dict] = None
    steps: Optional[List[PlanStepIn]] = None


class PlanStepOut(BaseModel):
    id: int
    step_key: str
    script_name: str
    script_version: str
    stage: str
    sort_order: int
    timeout_seconds: Optional[int] = None
    retry: int

    class Config:
        from_attributes = True


class PlanOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    failure_threshold: float
    lifecycle: dict
    next_plan_id: Optional[int] = None
    watcher_policy: Optional[dict] = None
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    steps: List[PlanStepOut] = []

    class Config:
        from_attributes = True


class PlanRunTrigger(BaseModel):
    device_ids: List[int]
    failure_threshold: Optional[float] = None


class PlanRunOut(BaseModel):
    id: int
    plan_id: int
    status: str
    failure_threshold: float
    run_type: str
    triggered_by: Optional[str] = None
    started_at: datetime
    ended_at: Optional[datetime] = None
    result_summary: Optional[dict] = None

    class Config:
        from_attributes = True


# ── Helpers ──────────────────────────────────────────────────────────────

def _validate_script_refs(db: Session, steps: list[PlanStepIn]) -> None:
    """Warn about PlanStep entries that reference non-existent or inactive Scripts.

    Does NOT reject the request — missing scripts may be registered later.
    """
    if not steps:
        return
    keys = {(s.script_name, s.script_version) for s in steps}
    from backend.models.script import Script as ScriptModel
    rows = db.execute(
        select(ScriptModel.name, ScriptModel.version).where(
            ScriptModel.is_active.is_(True),
            ScriptModel.name.in_({k[0] for k in keys}),
        )
    ).all()
    existing = {(r.name, r.version) for r in rows}
    missing = keys - existing
    if missing:
        formatted = [f"{n}:{v}" for n, v in sorted(missing)]
        logger.warning(
            "plan_script_refs_unknown missing=%s — Plan will still be created",
            formatted,
        )


def _validate_lifecycle(lifecycle: dict) -> None:
    is_valid, errors = validate_pipeline_def({"lifecycle": lifecycle})
    if not is_valid:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_LIFECYCLE", "errors": errors},
        )


def _plan_out(plan: Plan, steps: list) -> PlanOut:
    return PlanOut(
        id=plan.id,
        name=plan.name,
        description=plan.description,
        failure_threshold=plan.failure_threshold,
        lifecycle=plan.lifecycle or {},
        next_plan_id=plan.next_plan_id,
        watcher_policy=plan.watcher_policy,
        created_by=plan.created_by,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        steps=[PlanStepOut.model_validate(s) for s in steps],
    )


MAX_CHAIN_DEPTH = 20


def _validate_plan_dag(db: Session, plan_id: int,
                        next_plan_id: int | None) -> None:
    """Prevent DAG cycles.

    Takes a PostgreSQL advisory lock on *plan_id* to serialise concurrent
    chain modifications, then walks the ``next_plan_id`` chain up to
    ``MAX_CHAIN_DEPTH`` hops.  Self-loops and cycles are rejected.
    """
    if next_plan_id is None:
        return

    # Advisory lock: prevent concurrent chain edits on the same plan.
    # Skip on SQLite — no concurrent access, and the function doesn't exist.
    if not db.get_bind().dialect.name.startswith("sqlite"):
        db.execute(text("SELECT pg_advisory_xact_lock(:pid)"), {"pid": plan_id})

    if next_plan_id == plan_id:
        raise HTTPException(status_code=422, detail="next_plan_id cannot reference self")

    target = db.get(Plan, next_plan_id)
    if target is None:
        raise HTTPException(status_code=404,
                            detail=f"next_plan_id {next_plan_id} not found")

    visited = {plan_id}
    cursor = next_plan_id
    depth = 0
    while cursor is not None:
        if cursor in visited:
            raise HTTPException(
                status_code=422,
                detail=f"Cycle detected: plan {cursor} appears more than once in chain",
            )
        if cursor == plan_id:
            raise HTTPException(
                status_code=422,
                detail="next_plan_id creates a cycle back to the current plan",
            )
        visited.add(cursor)
        depth += 1
        if depth > MAX_CHAIN_DEPTH:
            raise HTTPException(
                status_code=422,
                detail=f"Chain exceeds max depth of {MAX_CHAIN_DEPTH}",
            )
        nxt = db.get(Plan, cursor)
        cursor = nxt.next_plan_id if nxt else None


# ── CRUD ─────────────────────────────────────────────────────────────────

@router.post("/plans", response_model=ApiResponse[PlanOut], status_code=201)
def create_plan(
    payload: PlanCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    _validate_lifecycle(payload.lifecycle)
    _validate_plan_dag(db, 0, payload.next_plan_id)
    _validate_script_refs(db, payload.steps)

    now = datetime.now(timezone.utc)
    plan = Plan(
        name=payload.name,
        description=payload.description,
        failure_threshold=payload.failure_threshold,
        lifecycle=payload.lifecycle,
        next_plan_id=payload.next_plan_id,
        watcher_policy=payload.watcher_policy,
        created_by=current_user.username if current_user else None,
        created_at=now,
        updated_at=now,
    )
    db.add(plan)
    db.flush()

    for s in payload.steps:
        db.add(PlanStep(
            plan_id=plan.id,
            step_key=s.step_key,
            script_name=s.script_name,
            script_version=s.script_version,
            stage=s.stage,
            sort_order=s.sort_order,
            timeout_seconds=s.timeout_seconds,
            retry=s.retry,
            created_at=now,
        ))

    db.commit()
    db.refresh(plan)
    steps = db.query(PlanStep).filter(PlanStep.plan_id == plan.id)\
        .order_by(PlanStep.stage, PlanStep.sort_order).all()
    return ok(_plan_out(plan, steps))


@router.get("/plans", response_model=ApiResponse[List[PlanOut]])
def list_plans(
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    plans = db.query(Plan).order_by(Plan.created_at.desc())\
        .offset(skip).limit(limit).all()

    if not plans:
        return ok([])

    plan_ids = [p.id for p in plans]
    all_steps = db.query(PlanStep).filter(PlanStep.plan_id.in_(plan_ids))\
        .order_by(PlanStep.stage, PlanStep.sort_order).all()
    steps_by_plan: dict[int, list] = {}
    for s in all_steps:
        steps_by_plan.setdefault(s.plan_id, []).append(s)

    return ok([_plan_out(p, steps_by_plan.get(p.id, [])) for p in plans])


@router.get("/plans/{plan_id}", response_model=ApiResponse[PlanOut])
def get_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")
    steps = db.query(PlanStep).filter(PlanStep.plan_id == plan_id)\
        .order_by(PlanStep.stage, PlanStep.sort_order).all()
    return ok(_plan_out(plan, steps))


@router.put("/plans/{plan_id}", response_model=ApiResponse[PlanOut])
def update_plan(
    plan_id: int,
    payload: PlanUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")

    if payload.name is not None:
        plan.name = payload.name
    if payload.description is not None:
        plan.description = payload.description
    if payload.failure_threshold is not None:
        plan.failure_threshold = payload.failure_threshold
    if payload.lifecycle is not None:
        _validate_lifecycle(payload.lifecycle)
        plan.lifecycle = payload.lifecycle
    if payload.watcher_policy is not None:
        plan.watcher_policy = payload.watcher_policy

    # DAG validation for next_plan_id changes
    fields_set = getattr(payload, "model_fields_set", set())
    if "next_plan_id" in fields_set:
        _validate_plan_dag(db, plan_id, payload.next_plan_id)
        plan.next_plan_id = payload.next_plan_id

    plan.updated_at = datetime.now(timezone.utc)

    # Step replacement
    if payload.steps is not None:
        _validate_script_refs(db, payload.steps)
        db.execute(text("DELETE FROM plan_step WHERE plan_id = :pid"), {"pid": plan_id})
        now = datetime.now(timezone.utc)
        for s in payload.steps:
            db.add(PlanStep(
                plan_id=plan.id,
                step_key=s.step_key,
                script_name=s.script_name,
                script_version=s.script_version,
                stage=s.stage,
                sort_order=s.sort_order,
                timeout_seconds=s.timeout_seconds,
                retry=s.retry,
                created_at=now,
            ))

    db.commit()
    db.refresh(plan)
    steps = db.query(PlanStep).filter(PlanStep.plan_id == plan_id)\
        .order_by(PlanStep.stage, PlanStep.sort_order).all()
    return ok(_plan_out(plan, steps))


@router.delete("/plans/{plan_id}", response_model=ApiResponse[dict])
def delete_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")

    active_run = db.query(PlanRun).filter(
        PlanRun.plan_id == plan_id, PlanRun.status == "RUNNING"
    ).first()
    if active_run:
        raise HTTPException(
            status_code=409, detail="cannot delete plan with active runs"
        )

    db.delete(plan)
    db.commit()
    return ok({"deleted": plan_id})


# ── Dispatch ─────────────────────────────────────────────────────────────

@router.post("/plans/{plan_id}/run/preview", response_model=ApiResponse[dict])
def preview_plan_run(
    plan_id: int,
    payload: PlanRunTrigger,
    db: Session = Depends(get_db),
):
    try:
        preview = preview_plan_dispatch_sync(
            plan_id=plan_id,
            device_ids=payload.device_ids,
            db=db,
        )
    except PlanDispatchError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ok(preview)


@router.post("/plans/{plan_id}/run", response_model=ApiResponse[PlanRunOut])
def run_plan(
    plan_id: int,
    payload: PlanRunTrigger,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        pr = dispatch_plan_sync(
            plan_id=plan_id,
            device_ids=payload.device_ids,
            triggered_by=current_user.username if current_user else "api",
            db=db,
            run_type="MANUAL",
            failure_threshold_override=payload.failure_threshold,
        )
    except PlanDispatchError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ok(PlanRunOut(
        id=pr.id, plan_id=pr.plan_id, status=pr.status,
        failure_threshold=pr.failure_threshold, run_type=pr.run_type,
        triggered_by=pr.triggered_by, started_at=pr.started_at,
        ended_at=pr.ended_at, result_summary=pr.result_summary,
    ))
