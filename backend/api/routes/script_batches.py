"""Script batch user-facing endpoints: dispatch, list, detail."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.response import ApiResponse
from backend.core.database import get_async_db
from backend.services.script_batch_service import (
    BatchError,
    create_batches,
    get_batch_detail,
    list_batches,
    rerun_batch,
)

router = APIRouter(prefix="/api/v1/script-batches", tags=["script-batches"])


class ScriptBatchItemIn(BaseModel):
    script_name: str
    version: str = ""
    params: dict = Field(default_factory=dict)
    timeout_seconds: int = Field(default=300, ge=1)


class ScriptBatchCreateIn(BaseModel):
    name: Optional[str] = None
    device_ids: List[int] = Field(..., min_length=1)
    items: List[ScriptBatchItemIn] = Field(..., min_length=1)
    sequence_id: Optional[int] = None
    on_failure: str = Field(default="stop", pattern="^(stop|continue)$")


class ScriptRunOut(BaseModel):
    id: int
    batch_id: int
    item_index: int
    script_name: str
    script_version: str
    params_json: dict
    status: str
    exit_code: Optional[int]
    stdout: Optional[str]
    stderr: Optional[str]
    metrics_json: Optional[dict]
    started_at: Optional[str]
    ended_at: Optional[str]

    class Config:
        from_attributes = True


class ScriptBatchOut(BaseModel):
    id: int
    name: Optional[str]
    sequence_id: Optional[int]
    device_id: int
    device_serial: str = ""
    device_model: Optional[str] = None
    host_id: Optional[str]
    host_name: Optional[str] = None
    status: str
    on_failure: str
    watcher_started_at: Optional[str]
    watcher_stopped_at: Optional[str]
    watcher_capability: Optional[str]
    log_signal_count: int
    started_at: Optional[str]
    ended_at: Optional[str]
    created_at: Optional[str]
    runs: List[ScriptRunOut] = []

    class Config:
        from_attributes = True


class ScriptBatchListItem(BaseModel):
    id: int
    name: Optional[str]
    device_id: int
    device_serial: str = ""
    host_id: Optional[str]
    status: str
    step_count: int = 0
    script_names: str = ""
    started_at: Optional[str]
    ended_at: Optional[str]
    created_at: Optional[str]

    class Config:
        from_attributes = True


@router.post("", status_code=201)
async def dispatch_batches(
    body: ScriptBatchCreateIn,
    db: AsyncSession = Depends(get_async_db),
):
    try:
        batches = await create_batches(
            device_ids=body.device_ids,
            items=[item.model_dump() for item in body.items],
            db=db,
            sequence_id=body.sequence_id,
            on_failure=body.on_failure,
            name=body.name,
        )
    except BatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Reload with relationships
    result = []
    for b in batches:
        detail = await get_batch_detail(b.id, db)
        if detail:
            result.append(detail)
    return ApiResponse(data=[_batch_to_out(b) for b in result])


@router.get("")
async def list_script_batches(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    device_id: Optional[int] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_async_db),
):
    rows, total = await list_batches(db, skip=skip, limit=limit, device_id=device_id, status=status)

    items = []
    for b in rows:
        runs = sorted(b.runs, key=lambda r: r.item_index) if b.runs else []
        items.append(ScriptBatchListItem(
            id=b.id,
            name=b.name,
            device_id=b.device_id,
            device_serial=b.device.serial if b.device else "",
            host_id=b.host_id,
            status=b.status,
            step_count=len(runs),
            script_names=" → ".join(r.script_name for r in runs),
            started_at=b.started_at.isoformat() if b.started_at else None,
            ended_at=b.ended_at.isoformat() if b.ended_at else None,
            created_at=b.created_at.isoformat() if b.created_at else None,
        ))

    return ApiResponse(data={"items": [i.model_dump() for i in items], "total": total})


@router.post("/{batch_id}/rerun", status_code=201)
async def rerun_script_batch(batch_id: int, db: AsyncSession = Depends(get_async_db)):
    batch = await rerun_batch(batch_id, db)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return ApiResponse(data=_batch_to_out(batch))


@router.get("/{batch_id}")
async def get_script_batch(batch_id: int, db: AsyncSession = Depends(get_async_db)):
    batch = await get_batch_detail(batch_id, db)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return ApiResponse(data=_batch_to_out(batch))


def _batch_to_out(b: ScriptBatch) -> dict:
    runs = sorted(b.runs, key=lambda r: r.item_index) if b.runs else []
    return ScriptBatchOut(
        id=b.id,
        name=b.name,
        sequence_id=b.sequence_id,
        device_id=b.device_id,
        device_serial=b.device.serial if b.device else "",
        device_model=b.device.model if b.device else None,
        host_id=b.host_id,
        host_name=b.host.name if b.host else None,
        status=b.status,
        on_failure=b.on_failure,
        watcher_started_at=b.watcher_started_at.isoformat() if b.watcher_started_at else None,
        watcher_stopped_at=b.watcher_stopped_at.isoformat() if b.watcher_stopped_at else None,
        watcher_capability=b.watcher_capability,
        log_signal_count=b.log_signal_count or 0,
        started_at=b.started_at.isoformat() if b.started_at else None,
        ended_at=b.ended_at.isoformat() if b.ended_at else None,
        created_at=b.created_at.isoformat() if b.created_at else None,
        runs=[
            ScriptRunOut(
                id=r.id, batch_id=r.batch_id, item_index=r.item_index,
                script_name=r.script_name, script_version=r.script_version,
                params_json=r.params_json or {}, status=r.status,
                exit_code=r.exit_code, stdout=r.stdout, stderr=r.stderr,
                metrics_json=r.metrics_json,
                started_at=r.started_at.isoformat() if r.started_at else None,
                ended_at=r.ended_at.isoformat() if r.ended_at else None,
            )
            for r in runs
        ],
    ).model_dump()
