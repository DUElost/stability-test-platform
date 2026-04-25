"""Script Catalog API."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import distinct
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.api.response import ApiResponse, ok
from backend.core.database import get_db
from backend.models.script import Script
from backend.services.script_catalog import scan_script_root

router = APIRouter(prefix="/api/v1/scripts", tags=["scripts"])


class ScriptCreate(BaseModel):
    name: str
    display_name: Optional[str] = None
    category: Optional[str] = None
    script_type: str
    version: str
    nfs_path: str
    entry_point: Optional[str] = ""
    content_sha256: str
    param_schema: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    description: Optional[str] = None


class ScriptUpdate(BaseModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    category: Optional[str] = None
    script_type: Optional[str] = None
    version: Optional[str] = None
    nfs_path: Optional[str] = None
    entry_point: Optional[str] = None
    content_sha256: Optional[str] = None
    param_schema: Optional[Dict[str, Any]] = None
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
    entry_point: Optional[str]
    content_sha256: str
    param_schema: Dict[str, Any]
    is_active: bool
    description: Optional[str]
    created_at: datetime
    updated_at: datetime


def _script_root() -> str:
    explicit = os.getenv("STP_SCRIPT_ROOT")
    if explicit:
        return explicit
    return str(Path(os.getenv("STP_NFS_ROOT", "/mnt/storage/test-platform")) / "scripts")


def _script_out(script: Script) -> ScriptOut:
    return ScriptOut(
        id=script.id,
        name=script.name,
        display_name=script.display_name,
        category=script.category,
        script_type=script.script_type,
        version=script.version,
        nfs_path=script.nfs_path,
        entry_point=script.entry_point,
        content_sha256=script.content_sha256,
        param_schema=script.param_schema or {},
        is_active=script.is_active,
        description=script.description,
        created_at=script.created_at,
        updated_at=script.updated_at,
    )


@router.get("/categories", response_model=ApiResponse[List[str]])
def list_script_categories(db: Session = Depends(get_db)):
    rows = (
        db.query(distinct(Script.category))
        .filter(Script.category.isnot(None))
        .order_by(Script.category)
        .all()
    )
    return ok([row[0] for row in rows if row[0]])


@router.post("/scan", response_model=ApiResponse[dict])
def scan_scripts(db: Session = Depends(get_db)):
    try:
        result = scan_script_root(db, _script_root())
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ok(result.to_dict())


@router.get("", response_model=ApiResponse[List[ScriptOut]])
def list_scripts(
    is_active: Optional[bool] = None,
    category: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = db.query(Script).order_by(Script.name, Script.version)
    if is_active is not None:
        query = query.filter(Script.is_active.is_(is_active))
    if category is not None:
        query = query.filter(Script.category == category)
    return ok([_script_out(script) for script in query.all()])


@router.post("", response_model=ApiResponse[ScriptOut], status_code=201)
def create_script(payload: ScriptCreate, db: Session = Depends(get_db)):
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

    now = datetime.utcnow()
    script = Script(
        name=payload.name,
        display_name=payload.display_name,
        category=payload.category,
        script_type=payload.script_type,
        version=payload.version,
        nfs_path=payload.nfs_path,
        entry_point=payload.entry_point,
        content_sha256=payload.content_sha256,
        param_schema=payload.param_schema,
        is_active=payload.is_active,
        description=payload.description,
        created_at=now,
        updated_at=now,
    )
    db.add(script)
    try:
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
def get_script(script_id: int, db: Session = Depends(get_db)):
    script = db.get(Script, script_id)
    if script is None:
        raise HTTPException(status_code=404, detail="script not found")
    return ok(_script_out(script))


@router.put("/{script_id}", response_model=ApiResponse[ScriptOut])
def update_script(script_id: int, payload: ScriptUpdate, db: Session = Depends(get_db)):
    script = db.get(Script, script_id)
    if script is None:
        raise HTTPException(status_code=404, detail="script not found")

    next_name = payload.name if payload.name is not None else script.name
    next_version = payload.version if payload.version is not None else script.version
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

    for field in (
        "name",
        "display_name",
        "category",
        "script_type",
        "version",
        "nfs_path",
        "entry_point",
        "content_sha256",
        "param_schema",
        "is_active",
        "description",
    ):
        value = getattr(payload, field)
        if value is not None:
            setattr(script, field, value)
    script.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(script)
    return ok(_script_out(script))


@router.delete("/{script_id}", response_model=ApiResponse[dict])
def deactivate_script(script_id: int, db: Session = Depends(get_db)):
    script = db.get(Script, script_id)
    if script is None:
        raise HTTPException(status_code=404, detail="script not found")
    script.is_active = False
    script.updated_at = datetime.utcnow()
    db.commit()
    return ok({"deactivated": script_id})
