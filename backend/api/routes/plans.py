"""Plan API — ADR-0020.

Plan CRUD + run/preview endpoints.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import get_current_active_user, User
from backend.core.audit import record_audit
from backend.core.legacy_aee import LEGACY_AEE_SCRIPT_NAMES
from backend.core.database import get_db
from backend.core.pipeline_validator import validate_pipeline_def
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.models.project import Specialty, TestProject
from backend.models.suite import TestSuite
from backend.services.script_progress_capability import script_supports_progress
from backend.models.resource_pool import ResourcePool
from backend.services.plan_dispatcher_core import plan_steps_consumes_wifi
from backend.services.plan_dispatcher_sync import (
    PlanDispatchError,
    initial_dispatch_state,
    prepare_plan_run,
    preview_plan_dispatch_sync,
)

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
    # 停滞钟（#115 阶段 1）：多久无 PROGRESS 戳算卡死。None/0 = 关闭。
    stall_seconds: Optional[int] = Field(default=None, ge=0)
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
    # INIT→PATROL barrier 预算。None = 沿用 STP_BARRIER_TIMEOUT_SECONDS / 600s。
    # 含长耗时前置步骤的计划必须抬高：只有先到者在等，预算要覆盖同 host 的
    # init 落差 ≈ (ceil(设备数/permit_cap) − 1) × 单设备 init 耗时。
    barrier_timeout_seconds: Optional[int] = Field(default=None, ge=1)
    # #174: barrier 绝对硬顶（从首次等待起算；None = 不设上限，保持 #117 行为）
    barrier_max_wait_seconds: Optional[int] = Field(default=None, ge=1)
    auto_archive_interval_seconds: Optional[int] = Field(default=None, ge=1)
    next_plan_id: Optional[int] = None
    watcher_policy: Optional[dict] = None
    steps: List[PlanStepIn] = Field(default_factory=list)
    # ADR-0029 D2/D6（#405）：归属项目与专项，F2 口径传 key、数字 id 只留 DB 外键
    project_key: Optional[str] = None
    specialty_key: Optional[str] = None
    # ADR-0030 v1.4（#404 PR-B）：套件绑定，对外引用键 = 套件 name
    suite_name: Optional[str] = None


class PlanUpdate(BaseModel):
    """ADR-0020 §2：所有字段可选，但 lifecycle 已删除。"""
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    description: Optional[str] = None
    failure_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    patrol_interval_seconds: Optional[int] = Field(default=None, ge=1)
    timeout_seconds: Optional[int] = Field(default=None, ge=1)
    # INIT→PATROL barrier 预算。None = 沿用 STP_BARRIER_TIMEOUT_SECONDS / 600s。
    # 含长耗时前置步骤的计划必须抬高：只有先到者在等，预算要覆盖同 host 的
    # init 落差 ≈ (ceil(设备数/permit_cap) − 1) × 单设备 init 耗时。
    barrier_timeout_seconds: Optional[int] = Field(default=None, ge=1)
    # #174: 同 PlanCreate
    barrier_max_wait_seconds: Optional[int] = Field(default=None, ge=1)
    auto_archive_interval_seconds: Optional[int] = Field(default=None, ge=1)
    next_plan_id: Optional[int] = None
    watcher_policy: Optional[dict] = None
    steps: Optional[List[PlanStepIn]] = None
    # ADR-0029 D2/D6（#405）：语义随 fields_set——显式传 null = 清除归属
    project_key: Optional[str] = None
    specialty_key: Optional[str] = None
    # ADR-0030 v1.4（#404 PR-B）：同 fields_set 语义——显式 null = 解绑套件；
    # 解绑即回到 P0 文件真源模式（PR-C 起托管门禁不再适用）
    suite_name: Optional[str] = None
    # 乐观锁令牌(#268 多Worker):客户端带上加载时的 updated_at,不一致则 409,
    # 防两个浏览器基于同一旧版本互相覆盖(last-write-wins)。
    expected_updated_at: Optional[datetime] = None


class PlanChainTailCreate(BaseModel):
    """链尾追加(#281 P1/CR Major):创建新 Plan 并把链尾 next_plan_id 指向
    它,在单个事务内完成——不再产生孤立 Plan。"""
    model_config = ConfigDict(extra="forbid")

    name: str
    description: Optional[str] = None
    steps: List[PlanStepIn] = Field(default_factory=list)
    # 链尾版本令牌(乐观锁):客户端加载链尾时的 updated_at,与链尾当前值
    # 不一致则整体 409 回滚。客户端无法确定链尾(超出最近 200 条窗口)时
    # 可省略——服务端仍以行锁串行化并发追加,不会产生孤立 Plan。
    expected_updated_at: Optional[datetime] = None


class PlanStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    step_key: str
    script_name: str
    script_version: str
    stage: str
    sort_order: int
    timeout_seconds: Optional[int] = None
    stall_seconds: Optional[int] = None
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
    barrier_timeout_seconds: Optional[int] = None
    barrier_max_wait_seconds: Optional[int] = None
    auto_archive_interval_seconds: Optional[int] = None
    next_plan_id: Optional[int] = None
    watcher_policy: Optional[dict] = None
    # ADR-0029：当前归属项目（F2 口径，不暴露数字 project_id）
    project_key: Optional[str] = None
    specialty_key: Optional[str] = None
    # ADR-0030 v1.4：绑定套件（对外引用键 = name）
    suite_name: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    steps: List[PlanStepOut] = []


class PlanRunTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_ids: List[int] = Field(min_length=1)
    # Optional operator note stored in PlanRun.run_context["note"] (no DB column).
    note: Optional[str] = Field(default=None, max_length=500)
    # Optional per-execution WiFi choice. ``None`` = do not connect (default).
    # Credentials are NOT accepted inline — the operator picks a pre-configured
    # ``resource_pool`` (resource_type='wifi'), so ssid/password live in exactly
    # one place instead of being copied into every run's stored payload.
    wifi_pool_id: Optional[int] = Field(default=None, gt=0)

    @field_validator("device_ids")
    @classmethod
    def validate_unique_device_ids(cls, value: List[int]) -> List[int]:
        if any(device_id <= 0 for device_id in value):
            raise ValueError("device_ids must contain positive IDs")
        if len(value) != len(set(value)):
            raise ValueError("device_ids must be unique")
        return value

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class PlanRunSummaryOut(BaseModel):
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


def _validate_stall_seconds_capability(
    steps: list[PlanStepIn],
    db: Session,
) -> None:
    """#136: ``stall_seconds > 0`` 要求脚本版本已接入 PROGRESS 打戳。

    停滞钟只认 stderr 的 PROGRESS 戳；引用旧版脚本（如 monkey_setup v2.2.0
    及更早）时打开停滞钟会在长静默段误杀。能力来自 script 表
    ``capabilities`` 列（#171，由版本目录 capabilities.json 登记）。
    """
    unsafe = sorted({
        f"{s.script_name}:{s.script_version}"
        for s in steps
        if s.stall_seconds is not None
        and s.stall_seconds > 0
        and not script_supports_progress(db, s.script_name, s.script_version)
    })
    if unsafe:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "STALL_REQUIRES_PROGRESS_SCRIPT",
                "steps": unsafe,
                "message": (
                    "stall_seconds>0 要求脚本版本已接入 PROGRESS 打戳；"
                    "请升级脚本版本或关闭停滞钟"
                ),
            },
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
    barrier_timeout_seconds: int | None = None,
    barrier_max_wait_seconds: int | None = None,
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
    if barrier_timeout_seconds is not None:
        lifecycle["barrier_timeout_seconds"] = barrier_timeout_seconds
    if barrier_max_wait_seconds is not None:
        lifecycle["barrier_max_wait_seconds"] = barrier_max_wait_seconds
    # 停滞钟是逐步骤的,不配就整键不写 —— 否则 NULL 会让 schema 拒掉
    for step_def in lifecycle.get("init", []) + lifecycle.get("teardown", []):
        s = next((x for x in steps if x.step_key == step_def["step_id"]), None)
        if s is not None and s.stall_seconds is not None:
            step_def["stall_seconds"] = s.stall_seconds
    patrol = lifecycle.get("patrol")
    if patrol:
        for step_def in patrol.get("steps", []):
            s = next((x for x in steps if x.step_key == step_def["step_id"]), None)
            if s is not None and s.stall_seconds is not None:
                step_def["stall_seconds"] = s.stall_seconds
    return lifecycle


def _validate_assembled_lifecycle(
    steps: list[PlanStepIn],
    patrol_interval_seconds: int | None,
    timeout_seconds: int | None,
    barrier_timeout_seconds: int | None = None,
    barrier_max_wait_seconds: int | None = None,
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
        steps, patrol_interval_seconds, timeout_seconds, barrier_timeout_seconds,
        barrier_max_wait_seconds,
    )
    is_valid, errors = validate_pipeline_def({"lifecycle": lifecycle})
    if not is_valid:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_LIFECYCLE", "errors": errors},
        )


def _resolve_project_id(db: Session, project_key: Optional[str]) -> Optional[int]:
    if project_key is None:
        return None
    proj = db.query(TestProject).filter(TestProject.project_key == project_key).first()
    if proj is None:
        raise HTTPException(status_code=404, detail=f"project not found: {project_key}")
    return proj.id


def _resolve_specialty_id(db: Session, specialty_key: Optional[str]) -> Optional[int]:
    if specialty_key is None:
        return None
    spec = db.query(Specialty).filter(Specialty.key == specialty_key).first()
    if spec is None:
        raise HTTPException(status_code=404, detail=f"specialty not found: {specialty_key}")
    return spec.id


def _resolve_suite_id(db: Session, suite_name: Optional[str]) -> Optional[int]:
    """ADR-0030 v1.4（#404 PR-B）：套件对外引用键是 name（同 suites.py 口径）。"""
    if suite_name is None:
        return None
    suite = db.query(TestSuite).filter(TestSuite.name == suite_name).first()
    if suite is None:
        raise HTTPException(status_code=404, detail=f"suite not found: {suite_name}")
    return suite.id


@router.get("/specialties", response_model=ApiResponse[List[dict]])
def list_specialties(
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """D6 专项字典（#405 接线）：Plan 编辑器下拉与列表分组的数据源。

    字典是静态种子数据（mtbf / power-cycle / monkey），无写端点——变更走迁移。
    """
    rows = db.query(Specialty).order_by(Specialty.sort_order, Specialty.id).all()
    return ok([{"key": r.key, "display_name": r.display_name,
                "sort_order": r.sort_order} for r in rows])


def _plan_out(plan: Plan, steps: list) -> PlanOut:
    return PlanOut(
        id=plan.id,
        name=plan.name,
        description=plan.description,
        failure_threshold=plan.failure_threshold,
        patrol_interval_seconds=plan.patrol_interval_seconds,
        timeout_seconds=plan.timeout_seconds,
        barrier_timeout_seconds=plan.barrier_timeout_seconds,
        barrier_max_wait_seconds=plan.barrier_max_wait_seconds,
        auto_archive_interval_seconds=plan.auto_archive_interval_seconds,
        next_plan_id=plan.next_plan_id,
        watcher_policy=plan.watcher_policy,
        project_key=plan.project.project_key if plan.project else None,
        specialty_key=plan.specialty.key if plan.specialty else None,
        suite_name=plan.suite.name if plan.suite else None,
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
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    _validate_no_legacy_aee_scripts(payload.steps)
    _validate_assembled_lifecycle(
        payload.steps, payload.patrol_interval_seconds, payload.timeout_seconds,
        payload.barrier_timeout_seconds, payload.barrier_max_wait_seconds,
    )
    _validate_plan_dag(db, None, payload.next_plan_id)
    _validate_script_refs(db, payload.steps)
    _validate_stall_seconds_capability(payload.steps, db)

    now = datetime.now(timezone.utc)
    plan = Plan(
        name=payload.name,
        description=payload.description,
        failure_threshold=payload.failure_threshold,
        patrol_interval_seconds=payload.patrol_interval_seconds,
        timeout_seconds=payload.timeout_seconds,
        barrier_timeout_seconds=payload.barrier_timeout_seconds,
        barrier_max_wait_seconds=payload.barrier_max_wait_seconds,
        auto_archive_interval_seconds=payload.auto_archive_interval_seconds,
        next_plan_id=payload.next_plan_id,
        watcher_policy=payload.watcher_policy,
        # ADR-0029（#405）：归属在创建时写入——新 Plan 不再恒 NULL
        project_id=_resolve_project_id(db, payload.project_key),
        specialty_id=_resolve_specialty_id(db, payload.specialty_key),
        suite_id=_resolve_suite_id(db, payload.suite_name),
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
            stall_seconds=s.stall_seconds,
            retry=s.retry,
            enabled=s.enabled,
            created_at=now,
        ))

    record_audit(
        db,
        action="plan_created",
        resource_type="plan",
        resource_id=plan.id,
        username=current_user.username if current_user else None,
        user_id=current_user.id if current_user else None,
        details={
            "name": plan.name,
            "step_count": len(payload.steps),
            **({"project_key": payload.project_key} if payload.project_key else {}),
            **({"specialty_key": payload.specialty_key} if payload.specialty_key else {}),
            **({"suite_name": payload.suite_name} if payload.suite_name else {}),
        },
        request=request,
    )
    # 审计与主变更同事务提交(get_db 不自动 commit,#281 CR 意见)
    db.commit()
    db.refresh(plan)
    steps = db.query(PlanStep).filter(PlanStep.plan_id == plan.id)\
        .order_by(PlanStep.stage, PlanStep.sort_order).all()
    from backend.realtime.socketio_server import emit_plan_changed
    emit_plan_changed(plan.id, "created")
    return ok(_plan_out(plan, steps))


def _find_chain_tail(db: Session, plan_id: int) -> Plan:
    """沿 next_plan_id 走到链尾(带环保护);目标不存在时抛 404。"""
    seen: set[int] = set()
    cursor = db.get(Plan, plan_id)
    if cursor is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    while cursor.next_plan_id is not None and cursor.id not in seen:
        seen.add(cursor.id)
        nxt = db.get(Plan, cursor.next_plan_id)
        if nxt is None:
            # 断链(指向已删除/丢失的 Plan):停在当前节点,视为链尾
            break
        cursor = nxt
    return cursor


def _lock_chain_tail(db: Session, plan_id: int) -> Plan:
    """锁内逐节点走到真正链尾（PostgreSQL 语义）。

    #281 二轮审查 P1：不能信任加锁前读到的链尾——并发追加会让「刚读到的
    链尾」在拿到锁时已经不是链尾。每到一个节点先 ``FOR UPDATE`` 再
    ``db.refresh()``，锁内重新确认；``next_plan_id`` 非空则继续走到新尾并
    加锁，直到 ``next_plan_id`` 为空。SQLite 测试环境无 FOR UPDATE 语义，
    退化为无锁走读。
    """
    if db.get_bind().dialect.name.startswith("sqlite"):
        return _find_chain_tail(db, plan_id)
    seen: set[int] = set()
    cursor = db.get(Plan, plan_id)
    if cursor is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    while True:
        db.execute(text("SELECT id FROM plan WHERE id = :pid FOR UPDATE"), {"pid": cursor.id})
        db.refresh(cursor)  # 锁内重读:不信加锁前的值
        if cursor.next_plan_id is None:
            return cursor
        if cursor.id in seen or cursor.next_plan_id == cursor.id:
            # 环保护:回到已访问节点视为断链,当前节点即链尾
            return cursor
        seen.add(cursor.id)
        nxt = db.get(Plan, cursor.next_plan_id)
        if nxt is None:
            return cursor  # 断链(指向已删除/丢失的 Plan)
        cursor = nxt


@router.post("/plans/{plan_id}/append-chain-tail", response_model=ApiResponse[PlanOut], status_code=201)
def append_chain_tail(
    plan_id: int,
    payload: PlanChainTailCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """原子链尾追加(#281 P1/CR Major)。

    在一个事务内「锁定链尾 → 校验版本 → 创建新 Plan → 更新 next_plan_id」,
    任一校验失败整体回滚,不再产生孤立 Plan。此前前端先 POST /plans 再 PUT
    链尾:并发客户端先改链尾时 PUT 收到 409,但新 Plan 已提交成为孤立记录;
    链首不在最近 200 条列表时还会静默跳过连接并显示成功。
    """
    # 锁内走到真正链尾(#281 二轮 P1):每个节点 FOR UPDATE + refresh,
    # 不信加锁前的值——并发追加后原"链尾"已非尾时继续走到新尾并加锁。
    tail = _lock_chain_tail(db, plan_id)
    tail_steps = db.query(PlanStep).filter(PlanStep.plan_id == tail.id)\
        .order_by(PlanStep.stage, PlanStep.sort_order).all()
    _raise_if_hidden_legacy_aee_plan(tail, tail_steps)
    _require_plan_owner_or_admin(tail, current_user)

    # 乐观锁:令牌与「锁内确认的真正链尾」updated_at 不一致即 409,整体回滚
    # (新 Plan 不落库)。令牌省略时(链尾超出最近 200 条窗口)仍以行锁串行化。
    if payload.expected_updated_at is not None:
        expected = payload.expected_updated_at
        if expected.tzinfo is None:
            expected = expected.replace(tzinfo=timezone.utc)
        if tail.updated_at != expected.astimezone(timezone.utc):
            raise HTTPException(
                status_code=409,
                detail="plan chain tail was modified by another session; reload and retry",
            )

    _validate_no_legacy_aee_scripts(payload.steps)
    _validate_script_refs(db, payload.steps)
    _validate_stall_seconds_capability(payload.steps, db)
    _validate_assembled_lifecycle(
        payload.steps, None, None, None, None,
    )

    now = datetime.now(timezone.utc)
    new_plan = Plan(
        name=payload.name,
        description=payload.description,
        failure_threshold=0.05,
        patrol_interval_seconds=None,
        timeout_seconds=None,
        barrier_timeout_seconds=None,
        barrier_max_wait_seconds=None,
        auto_archive_interval_seconds=None,
        next_plan_id=None,
        watcher_policy=None,
        created_by=current_user.username,
        created_at=now,
        updated_at=now,
    )
    db.add(new_plan)
    db.flush()

    for s in payload.steps:
        db.add(PlanStep(
            plan_id=new_plan.id,
            step_key=s.step_key,
            script_name=s.script_name,
            script_version=s.script_version,
            stage=s.stage,
            sort_order=s.sort_order,
            timeout_seconds=s.timeout_seconds,
            stall_seconds=s.stall_seconds,
            retry=s.retry,
            enabled=s.enabled,
            created_at=now,
        ))

    tail.next_plan_id = new_plan.id
    tail.updated_at = now

    record_audit(
        db,
        action="plan_created",
        resource_type="plan",
        resource_id=new_plan.id,
        username=current_user.username,
        user_id=current_user.id,
        details={"name": new_plan.name, "step_count": len(payload.steps),
                 "via": "append_chain_tail", "chain_tail_of": tail.id},
        request=request,
    )
    record_audit(
        db,
        action="plan_updated",
        resource_type="plan",
        resource_id=tail.id,
        username=current_user.username,
        user_id=current_user.id,
        details={"changed": ["next_plan_id"], "via": "append_chain_tail"},
        request=request,
    )
    # 审计与主变更同事务提交:任一校验失败(409)整体回滚,新 Plan 不落库
    db.commit()
    db.refresh(new_plan)
    steps = db.query(PlanStep).filter(PlanStep.plan_id == new_plan.id)\
        .order_by(PlanStep.stage, PlanStep.sort_order).all()
    from backend.realtime.socketio_server import emit_plan_changed
    emit_plan_changed(tail.id, "updated")
    emit_plan_changed(new_plan.id, "created")
    return ok(_plan_out(new_plan, steps))


@router.get("/plans", response_model=ApiResponse[List[PlanOut]])
def list_plans(
    skip: int = 0,
    limit: int = 50,
    project_key: Optional[str] = Query(None, description="ADR-0029: filter by project key"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    plans = db.query(Plan)
    if project_key:
        # 未知 key 一律 404（与 projects 路由同语义）
        if db.query(TestProject).filter(TestProject.project_key == project_key).first() is None:
            raise HTTPException(status_code=404, detail="project not found")
        plans = plans.join(TestProject, Plan.project_id == TestProject.id) \
                     .filter(TestProject.project_key == project_key)
    plans = plans.order_by(Plan.created_at.desc())\
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
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    # 行锁:两个浏览器并发 PUT 同一 Plan 时串行化,配合下面的乐观锁令牌,
    # step 全量 DELETE+INSERT 替换不再出现"后到者覆盖先到者"的静默丢失。
    # (SQLite 测试环境无 FOR UPDATE 语义,跳过。)
    if not db.get_bind().dialect.name.startswith("sqlite"):
        db.execute(text("SELECT id FROM plan WHERE id = :pid FOR UPDATE"), {"pid": plan_id})

    plan = db.get(Plan, plan_id)
    steps = db.query(PlanStep).filter(PlanStep.plan_id == plan_id)\
        .order_by(PlanStep.stage, PlanStep.sort_order).all()
    _raise_if_hidden_legacy_aee_plan(plan, steps)
    _require_plan_owner_or_admin(plan, current_user)

    # 乐观锁(#281 CR 意见):expected_updated_at 为必填——缺省即拒绝,
    # 杜绝"旧客户端不带令牌绕过并发防护"的路径。
    if payload.expected_updated_at is None:
        raise HTTPException(
            status_code=422,
            detail="expected_updated_at is required for plan updates",
        )
    expected = payload.expected_updated_at
    if expected.tzinfo is None:
        expected = expected.replace(tzinfo=timezone.utc)
    if plan.updated_at != expected.astimezone(timezone.utc):
        raise HTTPException(
            status_code=409,
            detail="plan was modified by another session; reload and retry",
        )

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
    if "barrier_timeout_seconds" in fields_set:
        plan.barrier_timeout_seconds = payload.barrier_timeout_seconds
    if "barrier_max_wait_seconds" in fields_set:
        plan.barrier_max_wait_seconds = payload.barrier_max_wait_seconds
    if "auto_archive_interval_seconds" in fields_set:
        plan.auto_archive_interval_seconds = payload.auto_archive_interval_seconds
    if payload.watcher_policy is not None:
        plan.watcher_policy = payload.watcher_policy

    # ADR-0029（#405）：归属变更语义随 fields_set——显式 null = 清除；
    # 未提供的字段不动另一维。审计经下方 changed 字段名列表自然覆盖。
    if "project_key" in fields_set:
        plan.project_id = _resolve_project_id(db, payload.project_key)
    if "specialty_key" in fields_set:
        plan.specialty_id = _resolve_specialty_id(db, payload.specialty_key)
    # ADR-0030 v1.4（#404 PR-B）：显式 null = 解绑，回到 P0 文件真源模式
    if "suite_name" in fields_set:
        plan.suite_id = _resolve_suite_id(db, payload.suite_name)

    # DAG validation for next_plan_id changes
    if "next_plan_id" in fields_set:
        _validate_plan_dag(db, plan_id, payload.next_plan_id)
        plan.next_plan_id = payload.next_plan_id

    plan.updated_at = datetime.now(timezone.utc)

    # Step replacement
    if payload.steps is not None:
        _validate_no_legacy_aee_scripts(payload.steps)
        _validate_script_refs(db, payload.steps)
        _validate_stall_seconds_capability(payload.steps, db)
        _validate_assembled_lifecycle(
            payload.steps,
            plan.patrol_interval_seconds,
            plan.timeout_seconds,
            plan.barrier_timeout_seconds,
            plan.barrier_max_wait_seconds,
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
                stall_seconds=s.stall_seconds,
                retry=s.retry,
                enabled=s.enabled,
                created_at=now,
            ))
    elif {"patrol_interval_seconds", "timeout_seconds",
          "barrier_timeout_seconds"} & fields_set:
        _validate_assembled_lifecycle(
            steps,
            plan.patrol_interval_seconds,
            plan.timeout_seconds,
            plan.barrier_timeout_seconds,
        )

    record_audit(
        db,
        action="plan_updated",
        resource_type="plan",
        resource_id=plan.id,
        username=current_user.username,
        user_id=current_user.id,
        # 只记字段名不记值:watcher_policy 等可能含敏感配置
        details={"changed": sorted(payload.model_fields_set - {"expected_updated_at"}), "step_count": len(payload.steps or steps)},
        request=request,
    )
    # 审计与主变更同事务提交(get_db 不自动 commit,#281 CR 意见)
    db.commit()
    db.refresh(plan)
    steps = db.query(PlanStep).filter(PlanStep.plan_id == plan_id)\
        .order_by(PlanStep.stage, PlanStep.sort_order).all()
    from backend.realtime.socketio_server import emit_plan_changed
    emit_plan_changed(plan.id, "updated")
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
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    expected_updated_at: Optional[datetime] = Query(default=None),
):
    plan = db.get(Plan, plan_id)
    steps = db.query(PlanStep).filter(PlanStep.plan_id == plan_id)\
        .order_by(PlanStep.stage, PlanStep.sort_order).all()
    _raise_if_hidden_legacy_aee_plan(plan, steps)
    _require_plan_owner_or_admin(plan, current_user)
    _assert_plan_deletable(db, plan_id)

    # 删除乐观锁(#281 P2):与 update 的 expected_updated_at 对称——旧页面
    # 不能删除已被其他客户端修改的 Plan。令牌可省略(兼容旧客户端),携带
    # 且不匹配时 409。
    if expected_updated_at is not None:
        expected = expected_updated_at
        if expected.tzinfo is None:
            expected = expected.replace(tzinfo=timezone.utc)
        if plan.updated_at != expected.astimezone(timezone.utc):
            raise HTTPException(
                status_code=409,
                detail="plan was modified by another session; reload and retry",
            )

    plan_name = plan.name

    record_audit(
        db,
        action="plan_deleted",
        resource_type="plan",
        resource_id=plan_id,
        username=current_user.username,
        user_id=current_user.id,
        details={"name": plan_name},
        request=request,
    )
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
    from backend.realtime.socketio_server import emit_plan_changed
    emit_plan_changed(plan_id, "deleted")
    return ok({"deleted": plan_id})


# ── Dispatch ─────────────────────────────────────────────────────────────

def _require_active_wifi_pool(db: Session, pool_id: int) -> None:
    """Reject the run up front if the chosen WiFi pool is gone or disabled.

    Without this the mistake would only surface inside the admission pump as an
    ``AllocationError``, i.e. after the PlanRun is already QUEUED.
    """
    pool = db.get(ResourcePool, pool_id)
    if pool is None or pool.resource_type != "wifi" or not pool.is_active:
        raise HTTPException(
            status_code=400,
            detail=f"wifi_pool_id {pool_id} is not an active wifi resource pool",
        )


def _require_wifi_pool_matches_plan(db: Session, plan_id: int, pool_id: int) -> None:
    """Reject wifi_pool_id when the Plan has no step that can consume WiFi."""
    steps = (
        db.query(PlanStep)
        .filter(PlanStep.plan_id == plan_id)
        .order_by(PlanStep.stage, PlanStep.sort_order)
        .all()
    )
    if not plan_steps_consumes_wifi(steps):
        raise HTTPException(
            status_code=400,
            detail=(
                "wifi_pool_id requires a plan step that consumes WiFi "
                "(connect_wifi or monkey_setup)"
            ),
        )


@router.post("/plans/{plan_id}/run/preview", response_model=ApiResponse[dict])
def preview_plan_run(
    plan_id: int,
    payload: PlanRunTrigger,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    if payload.wifi_pool_id is not None:
        _require_active_wifi_pool(db, payload.wifi_pool_id)
        _require_wifi_pool_matches_plan(db, plan_id, payload.wifi_pool_id)
    try:
        preview = preview_plan_dispatch_sync(
            plan_id=plan_id,
            device_ids=payload.device_ids,
            db=db,
        )
    except PlanDispatchError as e:
        raise HTTPException(status_code=400, detail=e.detail()) from e
    return ok(preview)


@router.post("/plans/{plan_id}/run", response_model=ApiResponse[PlanRunSummaryOut])
def run_plan(
    plan_id: int,
    payload: PlanRunTrigger,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """ADR-0026 — MANUAL dispatch via admission queue.

    ``prepare_plan_run`` creates a ``QUEUED`` PlanRun; the admission pump
    verifies, syncs, and materialises jobs asynchronously.
    """
    run_context: dict = {"dispatch_state": initial_dispatch_state()}
    if payload.note:
        run_context["note"] = payload.note
    if payload.wifi_pool_id is not None:
        _require_active_wifi_pool(db, payload.wifi_pool_id)
        _require_wifi_pool_matches_plan(db, plan_id, payload.wifi_pool_id)
        run_context["wifi_pool_id"] = payload.wifi_pool_id

    try:
        pr = prepare_plan_run(
            plan_id=plan_id,
            device_ids=payload.device_ids,
            triggered_by=current_user.username if current_user else "api",
            db=db,
            run_type="MANUAL",
            run_context=run_context,
        )
    except PlanDispatchError as e:
        raise HTTPException(status_code=400, detail=e.detail()) from e

    logger.info(
        "manual_dispatch_queued plan=%d plan_run=%d devices=%d by=%s",
        plan_id, pr.id, len(payload.device_ids),
        current_user.username if current_user else "api",
    )
    return ok(PlanRunSummaryOut(
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
