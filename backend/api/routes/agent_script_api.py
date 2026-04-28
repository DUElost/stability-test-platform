"""Agent-facing script batch endpoints: claim, report, complete."""

from __future__ import annotations

import base64
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.response import ApiResponse, ok
from backend.api.routes.agent_api import _verify_agent
from backend.core.database import get_async_db
from backend.services.script_batch_service import (
    claim_batch,
    complete_batch,
    update_run_status,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agent/script-batches", tags=["agent-script-batches"])


class ScriptBatchClaimItem(BaseModel):
    item_index: int
    script_name: str
    script_version: str
    params: dict
    timeout_seconds: int = 300
    script_content: Optional[str] = None  # base64-encoded, for scripts not on Agent NFS


class ScriptBatchClaimOut(BaseModel):
    batch_id: int
    device_id: int
    device_serial: str
    host_id: str
    on_failure: str = "stop"
    items: List[ScriptBatchClaimItem] = []


class RunStatusIn(BaseModel):
    status: str = Field(..., pattern="^(RUNNING|COMPLETED|FAILED|SKIPPED)$")
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    metrics: Optional[dict] = None


class BatchCompleteIn(BaseModel):
    status: str = Field(..., pattern="^(COMPLETED|FAILED|PARTIAL)$")
    watcher_summary: Optional[dict] = None


# Pydantic v2 deferred annotation resolution: with `from __future__ import annotations`,
# forward references like List[ScriptBatchClaimItem] are stored as strings and must be
# resolved explicitly before model usage.
ScriptBatchClaimOut.model_rebuild()
ScriptBatchClaimItem.model_rebuild()
RunStatusIn.model_rebuild()
BatchCompleteIn.model_rebuild()


def _read_script_content_for_backend(nfs_path: str) -> Optional[str]:
    """Read and base64-encode script file from backend's local copy.

    Maps the Agent NFS path to the backend project directory.
    """
    if not nfs_path:
        return None

    rel = nfs_path
    for prefix in ("/opt/stability-test-agent/",):
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
            break

    # Resolve relative to the backend/agent/ directory
    backend_root = Path(__file__).resolve().parent.parent.parent  # backend/
    candidate = backend_root / rel
    if not candidate.is_file():
        # Try relative to project root
        candidate = backend_root.parent / rel

    if not candidate.is_file():
        return None

    try:
        raw = candidate.read_bytes()
        return base64.b64encode(raw).decode("ascii")
    except OSError:
        return None


# Map (script_name, script_version) -> nfs_path for content lookup
async def _resolve_nfs_paths(db: AsyncSession, runs) -> dict:
    from backend.models.script import Script
    from sqlalchemy import select
    names = list({r.script_name for r in runs})
    versions = list({r.script_version for r in runs})
    if not names:
        return {}
    rows = (await db.execute(
        select(Script.name, Script.version, Script.nfs_path).where(
            Script.name.in_(names),
            Script.version.in_(versions),
        )
    )).all()
    return {(r.name, r.version): r.nfs_path for r in rows}


@router.post("/claim")
async def claim_script_batch(
    host_id: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    import traceback
    try:
        batch = await claim_batch(host_id, db)
        if batch is None:
            return ok(None)

        runs = sorted(batch.runs, key=lambda r: r.item_index) if batch.runs else []
        device_serial = batch.device.serial if batch.device else ""

        # Resolve NFS paths for script content lookup
        path_map = await _resolve_nfs_paths(db, runs)

        return ok(ScriptBatchClaimOut(
            batch_id=batch.id,
            device_id=batch.device_id,
            device_serial=device_serial,
            host_id=batch.host_id or "",
            on_failure=batch.on_failure,
            items=[
                ScriptBatchClaimItem(
                    item_index=r.item_index,
                    script_name=r.script_name,
                    script_version=r.script_version,
                    params={k: v for k, v in (r.params_json or {}).items()
                            if not k.startswith("_")},
                    timeout_seconds=(r.params_json or {}).get("_timeout_seconds", 300),
                    script_content=_read_script_content_for_backend(
                        path_map.get((r.script_name, r.script_version), "")
                    ),
                )
                for r in runs
            ],
        ).model_dump())
    except Exception:
        logger.exception("claim_script_batch_handler_failed host=%s", host_id)
        raise


@router.post("/{batch_id}/runs/{item_index}/status")
async def report_run_status(
    batch_id: int,
    item_index: int,
    body: RunStatusIn,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    run = await update_run_status(
        batch_id=batch_id,
        item_index=item_index,
        status=body.status,
        db=db,
        exit_code=body.exit_code,
        stdout=body.stdout,
        stderr=body.stderr,
        metrics=body.metrics,
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return ok({"item_index": item_index, "status": body.status})


@router.post("/{batch_id}/complete")
async def complete_script_batch(
    batch_id: int,
    body: BatchCompleteIn,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    batch = await complete_batch(
        batch_id=batch_id,
        status=body.status,
        db=db,
        watcher_summary=body.watcher_summary,
    )
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    logger.info(
        "agent_script_batch_complete: batch=%d status=%s signals=%d",
        batch_id, body.status, body.watcher_summary.get("log_signal_count", 0) if body.watcher_summary else 0,
    )
    return ok({"batch_id": batch_id, "status": body.status})
