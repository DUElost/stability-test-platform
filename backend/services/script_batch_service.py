"""Script batch dispatch service: fan-out devices × script sequences."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models.host import Device
from backend.models.script import Script
from backend.models.script_batch import ScriptBatch, ScriptRun
from backend.models.script_sequence import ScriptSequence
from backend.services.resource_pool import (
    AllocationError,
    allocate_devices,
    create_allocations,
)

logger = logging.getLogger(__name__)


class BatchError(Exception):
    pass


async def _resolve_script_items(
    items: list[dict],
    db: AsyncSession,
) -> list[dict]:
    """Validate and enrich script items with version/defaults."""
    if not items:
        raise BatchError("items is required")

    script_names = {item["script_name"] for item in items}
    rows = (await db.execute(
        select(Script.name, Script.version).where(
            Script.name.in_(script_names),
            Script.is_active.is_(True),
        )
    )).all()
    active = {row.name: row.version for row in rows}

    resolved = []
    for i, item in enumerate(items):
        name = item["script_name"]
        version = item.get("version", "")
        if not version:
            version = active.get(name, "")
        if not version or name not in active:
            raise BatchError(f"Script not found or inactive: {name}:{version}")
        resolved.append({
            "item_index": i,
            "script_name": name,
            "script_version": version,
            "params": item.get("params", {}),
            "timeout_seconds": item.get("timeout_seconds", 300),
        })
    return resolved


async def create_batches(
    device_ids: list[int],
    items: list[dict],
    db: AsyncSession,
    sequence_id: int | None = None,
    on_failure: str = "stop",
    name: str | None = None,
) -> list[ScriptBatch]:
    """Create one ScriptBatch per device, each with ScriptRuns for every item."""
    if not device_ids:
        raise BatchError("device_ids is required")

    resolved = await _resolve_script_items(items, db)

    device_rows = (await db.execute(
        select(Device.id, Device.host_id).where(Device.id.in_(device_ids))
    )).all()
    device_host = {row.id: row.host_id for row in device_rows}

    # WiFi allocation
    wifi_map: Dict[int, dict] = {}
    try:
        assignments = await allocate_devices(db, device_ids, resource_type="wifi")
        for did, (_pool, params) in assignments.items():
            wifi_map[did] = params
    except AllocationError as exc:
        logger.info("script_batch_wifi_skipped: %s", exc)

    now = datetime.utcnow()
    batches = []
    for did in device_ids:
        batch = ScriptBatch(
            name=name,
            sequence_id=sequence_id,
            device_id=did,
            host_id=device_host.get(did),
            status="PENDING",
            on_failure=on_failure,
            created_at=now,
        )
        db.add(batch)
        await db.flush()

        wifi_params = wifi_map.get(did, {})
        for item in resolved:
            params = dict(item["params"])
            # Store timeout inside params so claim endpoint can read it
            params["_timeout_seconds"] = item.get("timeout_seconds", 300)
            if wifi_params.get("ssid") and "connect_wifi" in item["script_name"]:
                params.setdefault("ssid", wifi_params["ssid"])
                params.setdefault("password", wifi_params.get("password", ""))

            run = ScriptRun(
                batch_id=batch.id,
                item_index=item["item_index"],
                script_name=item["script_name"],
                script_version=item["script_version"],
                params_json=params,
                status="PENDING",
                created_at=now,
            )
            db.add(run)

        batches.append(batch)

    await db.commit()
    logger.info(
        "script_batches_created: devices=%d items=%d batches=%d",
        len(device_ids), len(resolved), len(batches),
    )
    return batches


async def list_batches(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 50,
    device_id: int | None = None,
    status: str | None = None,
) -> tuple[list[ScriptBatch], int]:
    from sqlalchemy import func

    clauses = []
    if device_id:
        clauses.append(ScriptBatch.device_id == device_id)
    if status:
        clauses.append(ScriptBatch.status == status)

    total = (await db.execute(
        select(func.count(ScriptBatch.id)).where(*clauses)
    )).scalar() or 0

    rows = (await db.execute(
        select(ScriptBatch)
        .where(*clauses)
        .order_by(ScriptBatch.created_at.desc())
        .offset(skip)
        .limit(limit)
        .options(selectinload(ScriptBatch.device), selectinload(ScriptBatch.runs))
    )).scalars().all()

    return list(rows), total


async def get_batch_detail(batch_id: int, db: AsyncSession) -> ScriptBatch | None:
    result = await db.execute(
        select(ScriptBatch)
        .where(ScriptBatch.id == batch_id)
        .options(
            selectinload(ScriptBatch.device),
            selectinload(ScriptBatch.host),
            selectinload(ScriptBatch.sequence),
            selectinload(ScriptBatch.runs),
        )
    )
    return result.scalar()


async def claim_batch(
    host_id: str,
    db: AsyncSession,
) -> ScriptBatch | None:
    """Claim the oldest PENDING batch for a device on the given host.

    Atomically transitions PENDING → RUNNING.
    """
    dev_result = await db.execute(
        select(Device.id).where(Device.host_id == host_id)
    )
    device_ids = dev_result.scalars().all()

    if not device_ids:
        return None

    batch_result = await db.execute(
        select(ScriptBatch)
        .where(
            ScriptBatch.device_id.in_(device_ids),
            ScriptBatch.status == "PENDING",
        )
        .order_by(ScriptBatch.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    batch = batch_result.scalar()

    if batch is None:
        return None

    batch.status = "RUNNING"
    batch.host_id = host_id
    batch.started_at = datetime.utcnow()
    await db.commit()

    # Reload with relationships
    return await get_batch_detail(batch.id, db)


async def update_run_status(
    batch_id: int,
    item_index: int,
    status: str,
    db: AsyncSession,
    exit_code: int | None = None,
    stdout: str | None = None,
    stderr: str | None = None,
    metrics: dict | None = None,
) -> ScriptRun | None:
    result = await db.execute(
        select(ScriptRun).where(
            ScriptRun.batch_id == batch_id,
            ScriptRun.item_index == item_index,
        )
    )
    run = result.scalar()
    if run is None:
        return None

    run.status = status
    if exit_code is not None:
        run.exit_code = exit_code
    if stdout is not None:
        run.stdout = stdout
    if stderr is not None:
        run.stderr = stderr
    if metrics is not None:
        run.metrics_json = metrics
    if status in ("COMPLETED", "FAILED", "SKIPPED"):
        run.ended_at = datetime.utcnow()
    elif status == "RUNNING":
        run.started_at = run.started_at or datetime.utcnow()

    await db.commit()
    return run


async def rerun_batch(batch_id: int, db: AsyncSession) -> ScriptBatch | None:
    """Re-run a batch with the same items and device."""
    original = await get_batch_detail(batch_id, db)
    if not original:
        return None

    items = []
    for run in sorted(original.runs, key=lambda r: r.item_index):
        items.append({
            "script_name": run.script_name,
            "version": run.script_version,
            "params": run.params_json or {},
            "timeout_seconds": (run.params_json or {}).get("_timeout_seconds", 300),
        })

    batches = await create_batches(
        device_ids=[original.device_id],
        items=items,
        db=db,
        sequence_id=original.sequence_id,
        on_failure=original.on_failure,
        name=original.name,
    )
    if batches:
        return await get_batch_detail(batches[0].id, db)
    return None


async def complete_batch(
    batch_id: int,
    status: str,
    db: AsyncSession,
    watcher_summary: dict | None = None,
) -> ScriptBatch | None:
    batch = await db.get(ScriptBatch, batch_id)
    if batch is None:
        return None

    batch.status = status
    batch.ended_at = datetime.utcnow()
    if watcher_summary:
        batch.watcher_capability = watcher_summary.get("watcher_capability")
        batch.log_signal_count = watcher_summary.get("log_signal_count", 0)
        # NB: watcher_started_at / watcher_stopped_at arrive as ISO strings
        # from Agent JSON; asyncpg rejects raw strings for TIMESTAMPTZ columns.
        # Convert explicitly before assignment.
        for field in ("watcher_started_at", "watcher_stopped_at"):
            raw = watcher_summary.get(field)
            if raw is None:
                continue
            if isinstance(raw, str):
                raw = datetime.fromisoformat(raw)
            if isinstance(raw, datetime):
                setattr(batch, field, raw)

    await db.commit()
    await db.refresh(batch)
    return batch
