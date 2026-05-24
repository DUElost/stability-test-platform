"""Plan dispatcher — ADR-0020.

Dispatches a Plan execution:
1. Load Plan + PlanStep rows
2. Build lifecycle pipeline_def with params from script.default_params
3. Create PlanRun
4. Allocate ResourcePool (WiFi) per device
5. Create JobInstance per (device × Plan) with plan_run_id / plan_id
"""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.pipeline_validator import validate_pipeline_def
from backend.models.device_lease import DeviceLease
from backend.models.enums import DeviceStatus, HostStatus, JobStatus, LeaseStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.models.resource_pool import ResourcePool
from backend.models.script import Script
from backend.services.resource_pool import (
    AllocationError,
    allocate_devices,
    create_allocations,
)
from backend.services.plan_dispatcher_core import (
    PlanDispatchError,
    build_lifecycle_from_steps as _build_lifecycle_from_steps,
    build_plan_snapshot as _build_plan_snapshot,
    build_preview as _build_preview,
    check_script_keys_complete as _check_script_keys_complete,
    inject_wifi_params as _inject_wifi_params,
    iter_lifecycle_steps as _iter_lifecycle_steps,
    script_defaults as _script_defaults,
)

logger = logging.getLogger(__name__)


async def _validate_dispatch_devices(
    db: AsyncSession, device_ids: list[int]
) -> None:
    """Async counterpart of :func:`plan_dispatcher_sync._validate_dispatch_devices_sync`.

    Walks the same 5-step priority ladder (not_found / no_host /
    device_offline / host_offline / active_lease) and raises a
    :class:`PlanDispatchError` carrying ``unavailable_devices`` on failure.
    """
    if not device_ids:
        raise PlanDispatchError("device_ids must not be empty")

    rows = (await db.execute(
        select(
            Device.id,
            Device.host_id,
            Device.status.label("device_status"),
            Host.status.label("host_status"),
        )
        .select_from(Device)
        .outerjoin(Host, Device.host_id == Host.id)
        .where(Device.id.in_(device_ids))
    )).all()
    device_snapshot = {row.id: row for row in rows}

    lease_rows = (await db.execute(
        select(DeviceLease.device_id, DeviceLease.job_id)
        .where(
            DeviceLease.device_id.in_(device_ids),
            DeviceLease.status == LeaseStatus.ACTIVE.value,
        )
    )).all()
    active_lease_by_device = {row.device_id: row.job_id for row in lease_rows}

    unavailable: list[dict] = []
    for did in device_ids:
        snap = device_snapshot.get(did)
        if snap is None:
            unavailable.append({"id": did, "reason": "not_found"})
            continue
        if snap.host_id is None:
            unavailable.append({"id": did, "reason": "no_host"})
            continue
        if snap.device_status == DeviceStatus.OFFLINE.value:
            unavailable.append({
                "id": did, "reason": "device_offline",
                "device_status": snap.device_status,
            })
            continue
        if snap.host_status == HostStatus.OFFLINE.value:
            unavailable.append({
                "id": did, "reason": "host_offline",
                "host_id": snap.host_id, "host_status": snap.host_status,
            })
            continue
        if did in active_lease_by_device:
            unavailable.append({
                "id": did, "reason": "active_lease",
                "lease_job_id": active_lease_by_device[did],
            })
            continue

    if unavailable:
        raise PlanDispatchError(
            f"Dispatch rejected: {len(unavailable)} device(s) unavailable",
            unavailable_devices=unavailable,
        )


async def _fetch_script_metadata(
    db: AsyncSession, steps: list[PlanStep]
) -> dict[tuple[str, str], dict[str, dict]]:
    """Batch-fetch script metadata for all referenced scripts."""
    keys = {(s.script_name, s.script_version) for s in steps}
    if not keys:
        return {}
    names = {k[0] for k in keys}
    rows = (await db.execute(
        select(
            Script.name,
            Script.version,
            Script.default_params,
            Script.param_schema,
            Script.nfs_path,
        ).where(
            Script.name.in_(names), Script.is_active.is_(True)
        )
    )).all()
    return {
        (r.name, r.version): {
            "default_params": r.default_params or {},
            "param_schema": r.param_schema or {},
            "nfs_path": r.nfs_path or "",
        }
        for r in rows
    }
async def preview_plan_dispatch(
    plan_id: int,
    device_ids: list[int],
    db: AsyncSession,
) -> dict[str, Any]:
    plan = await db.get(Plan, plan_id)
    if plan is None:
        raise PlanDispatchError(f"Plan {plan_id} not found")

    steps_result = await db.execute(
        select(PlanStep)
        .where(PlanStep.plan_id == plan_id)
        .order_by(PlanStep.stage, PlanStep.sort_order)
    )
    steps = steps_result.scalars().all()
    if not steps:
        raise PlanDispatchError(f"Plan {plan_id} has no steps")

    metadata = await _fetch_script_metadata(db, steps)
    missing = _check_script_keys_complete(steps, metadata)
    if missing:
        raise PlanDispatchError(
            f"Plan {plan_id}: scripts unavailable at preview: {', '.join(missing)}",
            missing_scripts=missing,
        )
    defaults = _script_defaults(metadata)
    lifecycle = _build_lifecycle_from_steps(plan, steps, defaults)
    return _build_preview(plan, lifecycle, device_ids)


async def dispatch_plan(
    plan_id: int,
    device_ids: list[int],
    triggered_by: str,
    db: AsyncSession,
    run_type: str = "MANUAL",
    run_context: dict | None = None,
    parent_plan_run_id: int | None = None,
    root_plan_run_id: int | None = None,
    chain_index: int | None = None,
) -> PlanRun:
    """Async wrapper — delegates to the sync gate-backed dispatch path (ADR-0021)."""
    from backend.core.database import SessionLocal
    from backend.services.plan_dispatcher_sync import dispatch_plan_sync

    def _do_dispatch() -> PlanRun:
        with SessionLocal() as sdb:
            return dispatch_plan_sync(
                plan_id=plan_id,
                device_ids=device_ids,
                triggered_by=triggered_by,
                db=sdb,
                run_type=run_type,
                run_context=run_context,
                parent_plan_run_id=parent_plan_run_id,
                root_plan_run_id=root_plan_run_id,
                chain_index=chain_index,
            )

    return await asyncio.to_thread(_do_dispatch)
