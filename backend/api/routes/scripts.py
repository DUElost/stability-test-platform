"""Script Catalog API."""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import distinct
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.api.response import ApiResponse, ok
from backend.api.error_helpers import raise_api_http_error
from backend.api.routes.auth import get_current_active_user, get_current_user, require_admin, User
from backend.core.agent_secret import AgentSecretNotConfiguredError, require_agent_secret
from backend.core.audit import record_audit
from backend.core.legacy_aee import LEGACY_AEE_SCRIPT_NAMES
from backend.core.database import get_db
from backend.models.plan import PlanStep
from backend.models.script import Script
from backend.services.script_catalog import scan_script_root

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/scripts", tags=["scripts"])


class ScriptCreate(BaseModel):
    name: str
    display_name: Optional[str] = None
    category: Optional[str] = None
    script_type: str
    version: str
    nfs_path: str
    content_sha256: str
    param_schema: Dict[str, Any] = Field(default_factory=dict)
    default_params: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    description: Optional[str] = None


class ScriptUpdate(BaseModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    category: Optional[str] = None
    script_type: Optional[str] = None
    version: Optional[str] = None
    nfs_path: Optional[str] = None
    content_sha256: Optional[str] = None
    param_schema: Optional[Dict[str, Any]] = None
    default_params: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None
    description: Optional[str] = None


class ScriptOut(BaseModel):
    id: int
    name: str
    display_name: Optional[str]
    category: Optional[str]
    script_type: str
    version: str
    nfs_path: str
    content_sha256: str
    param_schema: Dict[str, Any]
    default_params: Dict[str, Any]
    is_active: bool
    description: Optional[str]
    created_at: datetime
    updated_at: datetime


def _script_root() -> str:
    explicit = os.getenv("STP_SCRIPT_ROOT")
    if explicit:
        return explicit
    return str(Path(os.getenv("STP_NFS_ROOT", "/mnt/storage/test-platform")) / "scripts")


def _script_runtime_root() -> str | None:
    return os.getenv("STP_SCRIPT_RUNTIME_ROOT")


def _script_out(script: Script) -> ScriptOut:
    return ScriptOut(
        id=script.id,
        name=script.name,
        display_name=script.display_name,
        category=script.category,
        script_type=script.script_type,
        version=script.version,
        nfs_path=script.nfs_path,
        content_sha256=script.content_sha256,
        param_schema=script.param_schema or {},
        default_params=script.default_params or {},
        is_active=script.is_active,
        description=script.description,
        created_at=script.created_at,
        updated_at=script.updated_at,
    )


def _raise_if_legacy_aee_script(name: str, version: str) -> None:
    if name not in LEGACY_AEE_SCRIPT_NAMES:
        return
    raise HTTPException(
        status_code=422,
        detail={
            "code": "LEGACY_AEE_SCRIPTS_DISABLED",
            "scripts": [f"{name}:{version}"],
        },
    )


def _raise_if_hidden_legacy_aee_script_row(script: Script | None) -> None:
    if script is None:
        raise HTTPException(status_code=404, detail="script not found")
    if script.name in LEGACY_AEE_SCRIPT_NAMES:
        raise HTTPException(status_code=404, detail="script not found")


# ---------------------------------------------------------------------------
# Agent auth bypass — 允许 Agent 通过 X-Agent-Secret 读取脚本目录,
# 避免 ScriptRegistry 401 后退化到过期 SQLite 缓存。
# ---------------------------------------------------------------------------

def _try_verify_agent(
    x_agent_secret: Optional[str] = Header(None, alias="X-Agent-Secret"),
) -> bool:
    """若 X-Agent-Secret 头与 AGENT_SECRET 匹配则返回 True;未提供/未配置返回 False;不匹配抛 401。

    调用方: ``_agent_or_user`` 在用户认证前优先尝试本函数,命中则跳过用户认证。
    """
    provided = (x_agent_secret or "").strip()
    if not provided:
        return False
    try:
        expected = require_agent_secret()
    except AgentSecretNotConfiguredError:
        return False
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid agent secret")
    return True


def _require_auth(
    x_agent_secret: Optional[str] = Header(None, alias="X-Agent-Secret"),
    current_user: Optional[User] = Depends(get_current_user),
) -> None:
    """Agent 或用户任一认证通过即放行;两者都缺则 401。

    - X-Agent-Secret 有效 → 直接放行(Agent 路径)
    - X-Agent-Secret 无效/未提供 → 回退到 User Bearer/Cookie 认证
    - 两者都缺 → 401
    """
    if _try_verify_agent(x_agent_secret):
        return
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _referencing_plan_ids(db: Session, script: Script) -> list[int]:
    rows = (
        db.query(PlanStep.plan_id)
        .filter(
            PlanStep.script_name == script.name,
            PlanStep.script_version == script.version,
        )
        .distinct()
        .order_by(PlanStep.plan_id)
        .all()
    )
    return [row.plan_id for row in rows]


def _ensure_script_can_be_deactivated(db: Session, script: Script) -> None:
    if not script.is_active:
        return
    plan_ids = _referencing_plan_ids(db, script)
    if not plan_ids:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": "SCRIPT_STILL_REFERENCED",
            "message": "script is still referenced by plan steps; update those plans before deactivation",
            "script": f"{script.name}:{script.version}",
            "plan_ids": plan_ids,
        },
    )


@router.get("/categories", response_model=ApiResponse[List[str]])
def list_script_categories(
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    rows = (
        db.query(distinct(Script.category))
        .filter(
            Script.category.isnot(None),
            Script.name.notin_(tuple(LEGACY_AEE_SCRIPT_NAMES)),
        )
        .order_by(Script.category)
        .all()
    )
    return ok([row[0] for row in rows if row[0]])


@router.post("/scan", response_model=ApiResponse[dict])
def scan_scripts(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    request: Request = None,
):
    try:
        result = scan_script_root(db, _script_root(), _script_runtime_root())
    except FileNotFoundError as exc:
        raise_api_http_error(
            status_code=400,
            code="SCRIPT_ROOT_NOT_FOUND",
            message="script root not found or unreadable",
        )
    record_audit(
        db,
        action="scan",
        resource_type="script_catalog",
        details=result.to_dict(),
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    return ok(result.to_dict())


@router.get("", response_model=ApiResponse[List[ScriptOut]])
def list_scripts(
    is_active: Optional[bool] = None,
    category: Optional[str] = None,
    db: Session = Depends(get_db),
    _auth: None = Depends(_require_auth),
):
    query = (
        db.query(Script)
        .filter(Script.name.notin_(tuple(LEGACY_AEE_SCRIPT_NAMES)))
        .order_by(Script.name, Script.version)
    )
    if is_active is not None:
        query = query.filter(Script.is_active.is_(is_active))
    if category is not None:
        query = query.filter(Script.category == category)
    return ok([_script_out(script) for script in query.all()])


@router.post("", response_model=ApiResponse[ScriptOut], status_code=201)
def create_script(
    payload: ScriptCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    request: Request = None,
):
    _raise_if_legacy_aee_script(payload.name, payload.version)
    existing = (
        db.query(Script)
        .filter(Script.name == payload.name, Script.version == payload.version)
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"script name/version already exists: {payload.name} {payload.version}",
        )

    now = datetime.now(timezone.utc)
    script = Script(
        name=payload.name,
        display_name=payload.display_name,
        category=payload.category,
        script_type=payload.script_type,
        version=payload.version,
        nfs_path=payload.nfs_path,
        content_sha256=payload.content_sha256,
        param_schema=payload.param_schema,
        default_params=payload.default_params,
        is_active=payload.is_active,
        description=payload.description,
        created_at=now,
        updated_at=now,
    )
    db.add(script)
    try:
        db.flush()
        record_audit(
            db,
            action="create",
            resource_type="script",
            resource_id=script.id,
            details={"name": script.name, "version": script.version, "is_active": script.is_active},
            user_id=current_user.id,
            username=current_user.username,
            request=request,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"script name/version already exists: {payload.name} {payload.version}",
        )
    db.refresh(script)
    return ok(_script_out(script))


@router.get("/{script_id}", response_model=ApiResponse[ScriptOut])
def get_script(
    script_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    script = db.get(Script, script_id)
    _raise_if_hidden_legacy_aee_script_row(script)
    return ok(_script_out(script))


@router.put("/{script_id}", response_model=ApiResponse[ScriptOut])
def update_script(
    script_id: int,
    payload: ScriptUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    request: Request = None,
):
    script = db.get(Script, script_id)
    _raise_if_hidden_legacy_aee_script_row(script)

    next_name = payload.name if payload.name is not None else script.name
    next_version = payload.version if payload.version is not None else script.version
    _raise_if_legacy_aee_script(next_name, next_version)
    if (next_name, next_version) != (script.name, script.version):
        existing = (
            db.query(Script)
            .filter(
                Script.name == next_name,
                Script.version == next_version,
                Script.id != script_id,
            )
            .first()
        )
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=f"script name/version already exists: {next_name} {next_version}",
            )

    # ADR-0020: changing default_params on an existing version is rejected;
    # the caller must create a new version instead.
    if payload.default_params is not None and payload.default_params != (script.default_params or {}):
        raise HTTPException(
            status_code=422,
            detail="default_params cannot be changed on an existing version; create a new script version instead",
        )

    if payload.is_active is False:
        _ensure_script_can_be_deactivated(db, script)

    for field in (
        "name",
        "display_name",
        "category",
        "script_type",
        "version",
        "nfs_path",
        "content_sha256",
        "param_schema",
        "default_params",
        "is_active",
        "description",
    ):
        value = getattr(payload, field)
        if value is not None:
            setattr(script, field, value)
    script.updated_at = datetime.now(timezone.utc)
    record_audit(
        db,
        action="update",
        resource_type="script",
        resource_id=script.id,
        details={"name": script.name, "version": script.version, "is_active": script.is_active},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(script)
    return ok(_script_out(script))


class ScriptVersionCreate(BaseModel):
    version: str
    nfs_path: str
    content_sha256: str
    param_schema: Dict[str, Any] = Field(default_factory=dict)
    default_params: Dict[str, Any] = Field(...)
    description: Optional[str] = None


@router.post("/{name}/versions", response_model=ApiResponse[ScriptOut], status_code=201)
def create_script_version(
    name: str,
    payload: ScriptVersionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    request: Request = None,
):
    """Create a new version of an existing script.

    ``default_params`` is required — it defines the canonical defaults
    for this version and must not be changed after creation.
    """
    _raise_if_legacy_aee_script(name, payload.version)
    existing = (
        db.query(Script)
        .filter(Script.name == name, Script.version == payload.version)
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"script version already exists: {name} {payload.version}",
        )

    # Inherit category/script_type from the latest active version
    latest = (
        db.query(Script)
        .filter(Script.name == name, Script.is_active.is_(True))
        .order_by(Script.created_at.desc())
        .first()
    )
    if latest is None:
        raise HTTPException(status_code=404, detail=f"script '{name}' not found")

    now = datetime.now(timezone.utc)
    script = Script(
        name=name,
        display_name=latest.display_name,
        category=latest.category,
        script_type=latest.script_type,
        version=payload.version,
        nfs_path=payload.nfs_path,
        content_sha256=payload.content_sha256,
        param_schema=payload.param_schema,
        default_params=payload.default_params,
        is_active=True,
        description=payload.description,
        created_at=now,
        updated_at=now,
    )
    db.add(script)
    try:
        db.flush()
        record_audit(
            db,
            action="create_version",
            resource_type="script",
            resource_id=script.id,
            details={"name": script.name, "version": script.version},
            user_id=current_user.id,
            username=current_user.username,
            request=request,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"script version already exists: {name} {payload.version}",
        )
    db.refresh(script)
    return ok(_script_out(script))


@router.delete("/{script_id}", response_model=ApiResponse[dict])
def deactivate_script(
    script_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    request: Request = None,
):
    script = db.get(Script, script_id)
    _raise_if_hidden_legacy_aee_script_row(script)
    _ensure_script_can_be_deactivated(db, script)
    script.is_active = False
    script.updated_at = datetime.now(timezone.utc)
    record_audit(
        db,
        action="deactivate",
        resource_type="script",
        resource_id=script.id,
        details={"name": script.name, "version": script.version},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    return ok({"deactivated": script_id})
