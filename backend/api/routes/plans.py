"""Plan API — ADR-0020.

Plan CRUD + run/preview endpoints.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import get_current_active_user, User
from backend.core.legacy_aee import LEGACY_AEE_SCRIPT_NAMES
from backend.core.database import get_db
from backend.core.pipeline_validator import validate_pipeline_def
from backend.models.enums import PlanRunStatus
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.services.state_machine import PlanRunStateMachine
from backend.services.plan_dispatcher_sync import (
    PlanDispatchError,
    dispatch_plan_sync,
    initial_dispatch_state,
    prepare_plan_run,
    preview_plan_dispatch_sync,
)
from backend.tasks.saq_worker import EnqueueSyncError, enqueue_sync

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["plans"])

def _require_plan_owner_or_admin(plan: Plan, user: User) -> None:
    """Plan 写操作鉴权:admin 或 plan 的 created_by 才放行。

    Why: 审计 #8 提出 plans.py:441 delete_plan 仅需登录,任意 user 可删他人 Plan。
    How to apply: 任何 update/delete plan 端点都先调用此 helper。
    """
    if user.role == "admin":
        return
    owner = (plan.created_by or "").strip()
    if owner and owner == user.username:
        return
    raise HTTPException(
        status_code=403,
        detail="only the plan owner or an admin can modify this plan",
    )


# ── Schemas ──────────────────────────────────────────────────────────────

class PlanStepIn(BaseModel):
    step_key: str
    script_name: str
    script_version: str
    stage: str = Field(..., pattern="^(init|patrol|teardown)$")
    sort_order: int = 0
    timeout_seconds: Optional[int] = None
    retry: int = Field(default=0, ge=0, le=5)
    enabled: bool = True


class PlanCreate(BaseModel):
    """ADR-0020 §2：Plan 仅持有 step 行 + 直列字段，不再接受 lifecycle JSON。"""
    model_config = ConfigDict(extra="forbid")

    name: str
    description: Optional[str] = None
    failure_threshold: float = Field(default=0.05, ge=0.0, le=1.0)
    patrol_interval_seconds: Optional[int] = Field(default=None, ge=1)
    timeout_seconds: Optional[int] = Field(default=None, ge=1)
    auto_archive_interval_seconds: Optional[int] = Field(default=None, ge=1)
    next_plan_id: Optional[int] = None
    watcher_policy: Optional[dict] = None
    steps: List[PlanStepIn] = Field(default_factory=list)


class PlanUpdate(BaseModel):
    """ADR-0020 §2：所有字段可选，但 lifecycle 已删除。"""
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    description: Optional[str] = None
    failure_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    patrol_interval_seconds: Optional[int] = Field(default=None, ge=1)
    timeout_seconds: Optional[int] = Field(default=None, ge=1)
    auto_archive_interval_seconds: Optional[int] = Field(default=None, ge=1)
    next_plan_id: Optional[int] = None
    watcher_policy: Optional[dict] = None
    steps: Optional[List[PlanStepIn]] = None


class PlanStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    step_key: str
    script_name: str
    script_version: str
    stage: str
    sort_order: int
    timeout_seconds: Optional[int] = None
    retry: int
    enabled: bool


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str] = None
    failure_threshold: float
    patrol_interval_seconds: Optional[int] = None
    timeout_seconds: Optional[int] = None
    auto_archive_interval_seconds: Optional[int] = None
    next_plan_id: Optional[int] = None
    watcher_policy: Optional[dict] = None
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    steps: List[PlanStepOut] = []


class PlanRunTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_ids: List[int] = Field(min_length=1)

    @field_validator("device_ids")
    @classmethod
    def validate_unique_device_ids(cls, value: List[int]) -> List[int]:
        if any(device_id <= 0 for device_id in value):
            raise ValueError("device_ids must contain positive IDs")
        if len(value) != len(set(value)):
            raise ValueError("device_ids must be unique")
        return value


class PlanRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    plan_id: int
    status: str
    failure_threshold: float
    run_type: str
    triggered_by: Optional[str] = None
    started_at: datetime
    ended_at: Optional[datetime] = None
    result_summary: Optional[dict] = None
    # ADR-0021: dispatch gate progress lives under run_context.precheck.
    run_context: Optional[dict] = None
    plan_snapshot: Optional[dict] = None
    parent_plan_run_id: Optional[int] = None
    root_plan_run_id: Optional[int] = None
    chain_index: int = 0
    next_plan_triggered: bool = False


# ── Helpers ──────────────────────────────────────────────────────────────

def _validate_script_refs(db: Session, steps: list[PlanStepIn]) -> None:
    """Reject PlanStep entries that reference non-existent or inactive Scripts."""
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
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_SCRIPT_REFS", "missing": formatted},
        )


def _validate_no_legacy_aee_scripts(steps: list[PlanStepIn]) -> None:
    """Block new Plan definitions from introducing legacy AEE patrol scripts."""
    disabled = sorted({
        f"{step.script_name}:{step.script_version}"
        for step in steps
        if step.script_name in LEGACY_AEE_SCRIPT_NAMES
    })
    if disabled:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "LEGACY_AEE_SCRIPTS_DISABLED",
                "scripts": disabled,
            },
        )


def _plan_steps_include_legacy_aee_scripts(steps: list[PlanStep]) -> bool:
    return any(step.script_name in LEGACY_AEE_SCRIPT_NAMES for step in steps)


def _raise_if_hidden_legacy_aee_plan(plan: Plan | None, steps: list[PlanStep]) -> None:
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")
    if _plan_steps_include_legacy_aee_scripts(steps):
        raise HTTPException(status_code=404, detail="plan not found")


def _raise_if_hidden_next_plan(db: Session, plan: Plan | None, plan_id: int) -> None:
    if plan is None:
        raise HTTPException(
            status_code=404,
            detail=f"next_plan_id {plan_id} not found",
        )
    steps = db.query(PlanStep).filter(PlanStep.plan_id == plan_id).all()
    if _plan_steps_include_legacy_aee_scripts(steps):
        raise HTTPException(
            status_code=404,
            detail=f"next_plan_id {plan_id} not found",
        )


def _assemble_lifecycle_for_validation(
    steps: list[PlanStepIn],
    patrol_interval_seconds: int | None,
    timeout_seconds: int | None,
) -> dict:
    """ADR-0020 §2：从 PlanStep 行 + 直列字段组装 lifecycle，仅用于 ``validate_pipeline_def``。

    与 dispatcher 的最终生成逻辑保持一致；params 字段使用 ``{}`` 占位，因为
    脚本的 default_params 在校验阶段不重要（pipeline_validator 只验结构）。
    """
    lifecycle: dict = {"init": [], "teardown": []}
    patrol_steps: list[dict] = []
    for s in sorted(steps, key=lambda x: (x.stage, x.sort_order)):
        if s.enabled is False:
            continue
        step_def = {
            "step_id": s.step_key,
            "action": f"script:{s.script_name}",
            "version": s.script_version,
            "params": {},
            "timeout_seconds": s.timeout_seconds,
            "retry": s.retry,
        }
        if s.stage in ("init", "teardown"):
            lifecycle[s.stage].append(step_def)
        else:
            patrol_steps.append(step_def)
    if patrol_steps:
        lifecycle["patrol"] = {
            "interval_seconds": patrol_interval_seconds or 60,
            "steps": patrol_steps,
        }
    if timeout_seconds is not None:
        lifecycle["timeout_seconds"] = timeout_seconds
    return lifecycle


def _validate_assembled_lifecycle(
    steps: list[PlanStepIn],
    patrol_interval_seconds: int | None,
    timeout_seconds: int | None,
) -> None:
    """先组装、再用统一的 pipeline_validator 校验。"""
    has_patrol_steps = any(
        step.enabled is not False and step.stage == "patrol"
        for step in steps
    )
    if has_patrol_steps != (patrol_interval_seconds is not None):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_PATROL_CONFIGURATION",
                "message": (
                    "enabled patrol steps and patrol_interval_seconds "
                    "must either both exist or both be absent"
                ),
            },
        )
    lifecycle = _assemble_lifecycle_for_validation(
        steps, patrol_interval_seconds, timeout_seconds
    )
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
        patrol_interval_seconds=plan.patrol_interval_seconds,
        timeout_seconds=plan.timeout_seconds,
        auto_archive_interval_seconds=plan.auto_archive_interval_seconds,
        next_plan_id=plan.next_plan_id,
        watcher_policy=plan.watcher_policy,
        created_by=plan.created_by,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        steps=[PlanStepOut.model_validate(s) for s in steps],
    )


MAX_CHAIN_DEPTH = 20
_PLAN_DAG_ADVISORY_LOCK_KEY = 0x53545044


def _validate_plan_dag(db: Session, plan_id: int | None,
                        next_plan_id: int | None) -> None:
    """Prevent DAG cycles (ADR-0020 §2).

    锁定语义：
    - 更新已存在 Plan：对 ``plan_id`` 行加 advisory lock。
    - 创建新 Plan（``plan_id is None``）：若有 ``next_plan_id`` 则锁目标行，
      避免和"目标 Plan 自身正在被改 next_plan_id"的事务并发产生环。
      自身尚无 ID，无需锁；插入后由数据库 CHECK + 唯一索引兜底。
    - 自环：``plan_id is not None and next_plan_id == plan_id`` 直接 422。

    然后顺着 ``next_plan_id`` 走链，最多 ``MAX_CHAIN_DEPTH`` 跳。
    """
    if not db.get_bind().dialect.name.startswith("sqlite"):
        db.execute(
            text("SELECT pg_advisory_xact_lock(:pid)"),
            {"pid": _PLAN_DAG_ADVISORY_LOCK_KEY},
        )

    if next_plan_id is None:
        return

    # 自环（仅在 update 场景下有 plan_id）
    if plan_id is not None and next_plan_id == plan_id:
        raise HTTPException(status_code=422, detail="next_plan_id cannot reference self")

    target = db.get(Plan, next_plan_id)
    _raise_if_hidden_next_plan(db, target, next_plan_id)

    visited: set[int] = set()
    if plan_id is not None:
        visited.add(plan_id)
    cursor = next_plan_id
    depth = 0
    while cursor is not None:
        if cursor in visited:
            raise HTTPException(
                status_code=422,
                detail=f"Cycle detected: plan {cursor} appears more than once in chain",
            )
        if plan_id is not None and cursor == plan_id:
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
    _validate_no_legacy_aee_scripts(payload.steps)
    _validate_assembled_lifecycle(
        payload.steps, payload.patrol_interval_seconds, payload.timeout_seconds
    )
    _validate_plan_dag(db, None, payload.next_plan_id)
    _validate_script_refs(db, payload.steps)

    now = datetime.now(timezone.utc)
    plan = Plan(
        name=payload.name,
        description=payload.description,
        failure_threshold=payload.failure_threshold,
        patrol_interval_seconds=payload.patrol_interval_seconds,
        timeout_seconds=payload.timeout_seconds,
        auto_archive_interval_seconds=payload.auto_archive_interval_seconds,
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
            enabled=s.enabled,
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
    _current_user: User = Depends(get_current_active_user),
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

    visible_plans = [
        _plan_out(p, steps_by_plan.get(p.id, []))
        for p in plans
        if not _plan_steps_include_legacy_aee_scripts(steps_by_plan.get(p.id, []))
    ]
    return ok(visible_plans)


@router.get("/plans/{plan_id}", response_model=ApiResponse[PlanOut])
def get_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    plan = db.get(Plan, plan_id)
    steps = db.query(PlanStep).filter(PlanStep.plan_id == plan_id)\
        .order_by(PlanStep.stage, PlanStep.sort_order).all()
    _raise_if_hidden_legacy_aee_plan(plan, steps)
    return ok(_plan_out(plan, steps))


@router.put("/plans/{plan_id}", response_model=ApiResponse[PlanOut])
def update_plan(
    plan_id: int,
    payload: PlanUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    plan = db.get(Plan, plan_id)
    steps = db.query(PlanStep).filter(PlanStep.plan_id == plan_id)\
        .order_by(PlanStep.stage, PlanStep.sort_order).all()
    _raise_if_hidden_legacy_aee_plan(plan, steps)
    _require_plan_owner_or_admin(plan, current_user)

    if payload.name is not None:
        plan.name = payload.name
    if payload.description is not None:
        plan.description = payload.description
    if payload.failure_threshold is not None:
        plan.failure_threshold = payload.failure_threshold
    fields_set = getattr(payload, "model_fields_set", set())
    if "patrol_interval_seconds" in fields_set:
        plan.patrol_interval_seconds = payload.patrol_interval_seconds
    if "timeout_seconds" in fields_set:
        plan.timeout_seconds = payload.timeout_seconds
    if "auto_archive_interval_seconds" in fields_set:
        plan.auto_archive_interval_seconds = payload.auto_archive_interval_seconds
    if payload.watcher_policy is not None:
        plan.watcher_policy = payload.watcher_policy

    # DAG validation for next_plan_id changes
    if "next_plan_id" in fields_set:
        _validate_plan_dag(db, plan_id, payload.next_plan_id)
        plan.next_plan_id = payload.next_plan_id

    plan.updated_at = datetime.now(timezone.utc)

    # Step replacement
    if payload.steps is not None:
        _validate_no_legacy_aee_scripts(payload.steps)
        _validate_script_refs(db, payload.steps)
        _validate_assembled_lifecycle(
            payload.steps,
            plan.patrol_interval_seconds,
            plan.timeout_seconds,
        )
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
                enabled=s.enabled,
                created_at=now,
            ))
    elif {"patrol_interval_seconds", "timeout_seconds"} & fields_set:
        _validate_assembled_lifecycle(
            steps,
            plan.patrol_interval_seconds,
            plan.timeout_seconds,
        )

    db.commit()
    db.refresh(plan)
    steps = db.query(PlanStep).filter(PlanStep.plan_id == plan_id)\
        .order_by(PlanStep.stage, PlanStep.sort_order).all()
    return ok(_plan_out(plan, steps))


def _assert_plan_deletable(db: Session, plan_id: int) -> None:
    """Reject delete when FK dependents would block commit (avoid 500 IntegrityError)."""
    if db.query(PlanRun.id).filter(
        PlanRun.plan_id == plan_id, PlanRun.status == "RUNNING"
    ).first():
        raise HTTPException(
            status_code=409, detail="cannot delete plan with active runs"
        )

    run_count = db.query(func.count()).select_from(PlanRun).filter(
        PlanRun.plan_id == plan_id
    ).scalar()
    if run_count:
        raise HTTPException(
            status_code=409,
            detail=(
                f"cannot delete plan with {run_count} execution record(s); "
                "remove or archive plan runs first"
            ),
        )

    from backend.models.schedule import TaskSchedule

    sched_count = db.query(func.count()).select_from(TaskSchedule).filter(
        TaskSchedule.plan_id == plan_id
    ).scalar()
    if sched_count:
        raise HTTPException(
            status_code=409,
            detail="cannot delete plan referenced by task schedules",
        )

    chain_parent = db.query(Plan.id).filter(Plan.next_plan_id == plan_id).first()
    if chain_parent:
        raise HTTPException(
            status_code=409,
            detail=f"cannot delete plan referenced as next_plan by plan {chain_parent[0]}",
        )


@router.delete("/plans/{plan_id}", response_model=ApiResponse[dict])
def delete_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    plan = db.get(Plan, plan_id)
    steps = db.query(PlanStep).filter(PlanStep.plan_id == plan_id)\
        .order_by(PlanStep.stage, PlanStep.sort_order).all()
    _raise_if_hidden_legacy_aee_plan(plan, steps)
    _require_plan_owner_or_admin(plan, current_user)
    _assert_plan_deletable(db, plan_id)

    try:
        db.delete(plan)
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.exception("plan delete blocked by FK for plan_id=%s", plan_id)
        raise HTTPException(
            status_code=409,
            detail="cannot delete plan while related records still exist",
        ) from None
    return ok({"deleted": plan_id})


# ── Dispatch ─────────────────────────────────────────────────────────────

@router.post("/plans/{plan_id}/run/preview", response_model=ApiResponse[dict])
def preview_plan_run(
    plan_id: int,
    payload: PlanRunTrigger,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    try:
        preview = preview_plan_dispatch_sync(
            plan_id=plan_id,
            device_ids=payload.device_ids,
            db=db,
        )
    except PlanDispatchError as e:
        raise HTTPException(status_code=400, detail=e.detail())
    return ok(preview)


@router.post("/plans/{plan_id}/run", response_model=ApiResponse[PlanRunOut])
def run_plan(
    plan_id: int,
    payload: PlanRunTrigger,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """ADR-0021: MANUAL dispatch goes through the precheck gate.

    Steps:
      1. ``prepare_plan_run`` — create PlanRun row + plan_snapshot synchronously,
         status=RUNNING, run_context.dispatch_device_ids set.
      2. Enqueue SAQ task ``precheck_and_dispatch`` with the new plan_run_id;
         it verifies / syncs / re-verifies / dispatches asynchronously.
      3. Return the PlanRun row immediately.  The frontend can navigate to
         the PlanRun detail page and watch ``run_context.precheck`` evolve.
    """
    try:
        pr = prepare_plan_run(
            plan_id=plan_id,
            device_ids=payload.device_ids,
            triggered_by=current_user.username if current_user else "api",
            db=db,
            run_type="MANUAL",
            run_context={"dispatch_state": initial_dispatch_state()},
        )
    except PlanDispatchError as e:
        raise HTTPException(status_code=400, detail=e.detail())

    # ADR-0026 Step 3: V2 prepare queued the run — return immediately. The
    # queue pump (Step 4) owns admission; enqueueing the legacy SAQ gate here
    # would create dual ownership (reviewer boundary #2).
    if pr.status == PlanRunStatus.QUEUED.value:
        logger.info(
            "manual_dispatch_queued plan=%d plan_run=%d devices=%d by=%s",
            plan_id, pr.id, len(payload.device_ids),
            current_user.username if current_user else "api",
        )
        return ok(PlanRunOut(
            id=pr.id, plan_id=pr.plan_id, status=pr.status,
            failure_threshold=pr.failure_threshold, run_type=pr.run_type,
            triggered_by=pr.triggered_by, started_at=pr.started_at,
            ended_at=pr.ended_at, result_summary=pr.result_summary,
            run_context=pr.run_context, plan_snapshot=pr.plan_snapshot,
            parent_plan_run_id=pr.parent_plan_run_id,
            root_plan_run_id=pr.root_plan_run_id,
            chain_index=pr.chain_index or 0,
            next_plan_triggered=bool(pr.next_plan_triggered),
        ))

    assert pr.run_context["dispatch_state"]["enqueue_key"] == f"precheck:{pr.id}"

    try:
        enqueue_sync(
            "precheck_and_dispatch_task",
            key=f"precheck:{pr.id}",
            timeout=600,
            retries=1,
            required=True,
            plan_run_id=pr.id,
        )
    except EnqueueSyncError as exc:
        now = datetime.now(timezone.utc)
        PlanRunStateMachine.transition(pr, PlanRunStatus.FAILED, reason="dispatch_queue_unavailable")
        pr.ended_at = now
        run_ctx = dict(pr.run_context or {})
        dispatch_state = dict(run_ctx.get("dispatch_state") or {})
        dispatch_state["status"] = "failed"
        dispatch_state["last_error"] = str(exc)
        dispatch_state["completed_at"] = now.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        run_ctx["dispatch_state"] = dispatch_state
        pr.run_context = run_ctx
        pr.result_summary = {
            "precheck_failed": True,
            "reason": "dispatch_queue_unavailable",
            "error": str(exc),
        }
        flag_modified(pr, "run_context")
        db.commit()
        raise HTTPException(
            status_code=503,
            detail={
                "code": "DISPATCH_QUEUE_UNAVAILABLE",
                "message": (
                    "Dispatch queue is unavailable; plan run marked FAILED. "
                    "Ensure Redis and in-process SAQ worker are running."
                ),
                "plan_run_id": pr.id,
            },
        ) from exc

    logger.info(
        "manual_dispatch_enqueued plan=%d plan_run=%d devices=%d by=%s",
        plan_id, pr.id, len(payload.device_ids),
        current_user.username if current_user else "api",
    )

    return ok(PlanRunOut(
        id=pr.id, plan_id=pr.plan_id, status=pr.status,
        failure_threshold=pr.failure_threshold, run_type=pr.run_type,
        triggered_by=pr.triggered_by, started_at=pr.started_at,
        ended_at=pr.ended_at, result_summary=pr.result_summary,
        run_context=pr.run_context, plan_snapshot=pr.plan_snapshot,
        parent_plan_run_id=pr.parent_plan_run_id,
        root_plan_run_id=pr.root_plan_run_id,
        chain_index=pr.chain_index or 0,
        next_plan_triggered=bool(pr.next_plan_triggered),
    ))
