"""Script sequence template API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.api.response import ApiResponse, ok
from backend.core.database import get_db
from backend.models.script_sequence import ScriptSequence
from backend.services.script_execution import normalize_script_items, validate_active_scripts, validate_on_failure

router = APIRouter(prefix="/api/v1/script-sequences", tags=["script-sequences"])


class ScriptSequenceIn(BaseModel):
    name: str
    description: Optional[str] = None
    items: List[Dict[str, Any]] = Field(default_factory=list)
    on_failure: str = "stop"


class ScriptSequenceUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    items: Optional[List[Dict[str, Any]]] = None
    on_failure: Optional[str] = None


class ScriptSequenceOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    items: List[Dict[str, Any]]
    on_failure: str
    created_by: Optional[str]
    created_at: datetime
    updated_at: datetime


class ScriptSequenceList(BaseModel):
    items: List[ScriptSequenceOut]
    total: int
    skip: int
    limit: int


def _out(sequence: ScriptSequence) -> ScriptSequenceOut:
    return ScriptSequenceOut(
        id=sequence.id,
        name=sequence.name,
        description=sequence.description,
        items=sequence.items or [],
        on_failure=sequence.on_failure,
        created_by=sequence.created_by,
        created_at=sequence.created_at,
        updated_at=sequence.updated_at,
    )


@router.get("", response_model=ApiResponse[ScriptSequenceList])
def list_script_sequences(skip: int = 0, limit: int = 100, q: str | None = None, db: Session = Depends(get_db)):
    query = db.query(ScriptSequence).order_by(ScriptSequence.updated_at.desc(), ScriptSequence.id.desc())
    if q and q.strip():
        keyword = f"%{q.strip()}%"
        query = query.filter(or_(ScriptSequence.name.ilike(keyword), ScriptSequence.description.ilike(keyword)))
    total = query.count()
    rows = query.offset(skip).limit(limit).all()
    return ok(ScriptSequenceList(items=[_out(row) for row in rows], total=total, skip=skip, limit=limit))


@router.post("", response_model=ApiResponse[ScriptSequenceOut], status_code=201)
def create_script_sequence(payload: ScriptSequenceIn, db: Session = Depends(get_db)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name cannot be empty")
    on_failure = validate_on_failure(payload.on_failure)
    items = normalize_script_items(payload.items)
    validate_active_scripts(db, items)

    now = datetime.utcnow()
    sequence = ScriptSequence(
        name=name,
        description=payload.description,
        items=items,
        on_failure=on_failure,
        created_by="api",
        created_at=now,
        updated_at=now,
    )
    db.add(sequence)
    db.commit()
    db.refresh(sequence)
    return ok(_out(sequence))


@router.get("/{sequence_id}", response_model=ApiResponse[ScriptSequenceOut])
def get_script_sequence(sequence_id: int, db: Session = Depends(get_db)):
    sequence = db.get(ScriptSequence, sequence_id)
    if sequence is None:
        raise HTTPException(status_code=404, detail="script sequence not found")
    return ok(_out(sequence))


@router.put("/{sequence_id}", response_model=ApiResponse[ScriptSequenceOut])
def update_script_sequence(
    sequence_id: int,
    payload: ScriptSequenceUpdate,
    db: Session = Depends(get_db),
):
    sequence = db.get(ScriptSequence, sequence_id)
    if sequence is None:
        raise HTTPException(status_code=404, detail="script sequence not found")

    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="name cannot be empty")
        sequence.name = name
    if "description" in payload.model_fields_set:
        sequence.description = payload.description
    if payload.on_failure is not None:
        sequence.on_failure = validate_on_failure(payload.on_failure)
    if payload.items is not None:
        items = normalize_script_items(payload.items)
        validate_active_scripts(db, items)
        sequence.items = items
    sequence.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(sequence)
    return ok(_out(sequence))


@router.delete("/{sequence_id}", response_model=ApiResponse[dict])
def delete_script_sequence(sequence_id: int, db: Session = Depends(get_db)):
    sequence = db.get(ScriptSequence, sequence_id)
    if sequence is None:
        raise HTTPException(status_code=404, detail="script sequence not found")
    db.delete(sequence)
    db.commit()
    return ok({"deleted": sequence_id})
