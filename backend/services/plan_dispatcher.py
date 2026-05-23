"""Plan dispatcher — ADR-0020.

Dispatches a Plan execution:
1. Load Plan + PlanStep rows
2. Build lifecycle pipeline_def with params from script.default_params
3. Create PlanRun
4. Allocate ResourcePool (WiFi) per device
5. Create JobInstance per (device × Plan) with plan_run_id / plan_id
"""

from __future__ import annotations

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
        # ADR-0023 C1:async dispatcher 为一次性事务(SCHEDULE / async CHAIN),
        # 失败时尚未创建 PlanRun 行,直接 raise 由上游 except 捕获并 log。
        raise PlanDispatchError(
            f"Plan {plan_id}: scripts unavailable at dispatch: {', '.join(missing)}",
            missing_scripts=missing,
        )

    # #8: device 可用性校验(同 sync prepare 入口),SCHEDULE/CHAIN 同样需要早拒。
    await _validate_dispatch_devices(db, device_ids)

    defaults = _script_defaults(metadata)
    lifecycle = _build_lifecycle_from_steps(plan, steps, defaults)

    # Validate generated lifecycle
    is_valid, errors = validate_pipeline_def({"lifecycle": lifecycle})
    if not is_valid:
        raise PlanDispatchError(
            f"Plan {plan_id} generated invalid lifecycle: {'; '.join(errors)}"
        )

    # Device → host mapping
    device_rows = (await db.execute(
        select(Device.id, Device.host_id).where(Device.id.in_(device_ids))
    )).all()
    device_host_map = {row.id: row.host_id for row in device_rows}

    orphan_devices = [did for did in device_ids if not device_host_map.get(did)]
    if orphan_devices:
        # Why: 校验已覆盖 no_host;若仍走到这里说明并发 race。
        logger.warning(
            "plan_dispatch_devices_without_host: device_ids=%s", orphan_devices
        )

    # ── ResourcePool: allocate WiFi per device ──
    # ADR-0020 §"特殊注入：wifi 资源池"：仅当 lifecycle 中存在 connect_wifi step
    # 时才申请池配额，避免无谓的锁竞争。
    wifi_allocations: dict[int, dict] = {}
    if any(
        "connect_wifi" in (step.get("action") or "")
        for _, step in _iter_lifecycle_steps({"lifecycle": lifecycle})
    ):
        try:
            assignments = await allocate_devices(db, device_ids, resource_type="wifi")
            for device_id, (_pool, alloc_params) in assignments.items():
                wifi_allocations[device_id] = alloc_params
            logger.info(
                "plan_dispatch_wifi_allocated: devices=%d", len(device_ids)
            )
        except AllocationError as exc:
            logger.warning("plan_dispatch_wifi_allocation_skipped: %s", exc)

    effective_threshold = plan.failure_threshold
    plan_snapshot = _build_plan_snapshot(plan, steps, metadata, effective_threshold)

    pr = PlanRun(
        plan_id=plan.id,
        status="RUNNING",
        failure_threshold=effective_threshold,
        plan_snapshot=plan_snapshot,
        run_type=run_type,
        run_context=run_context,
        triggered_by=triggered_by,
        parent_plan_run_id=parent_plan_run_id,
        root_plan_run_id=root_plan_run_id,
        chain_index=chain_index or 0,
        started_at=datetime.now(timezone.utc),
    )
    db.add(pr)
    await db.flush()

    now = datetime.now(timezone.utc)
    job_device_pairs: dict[int, int] = {}

    for device_id in device_ids:
        wifi_params = wifi_allocations.get(device_id)
        resolved_pipeline = {"lifecycle": deepcopy(lifecycle)}
        if wifi_params:
            resolved_pipeline = _inject_wifi_params(resolved_pipeline, wifi_params)

        job = JobInstance(
            plan_run_id=pr.id,
            plan_id=plan.id,
            device_id=device_id,
            host_id=device_host_map.get(device_id),
            status=JobStatus.PENDING.value,
            pipeline_def=resolved_pipeline,
            created_at=now,
            updated_at=now,
        )
        db.add(job)
        await db.flush()
        job_device_pairs[job.id] = device_id

    # Persist ResourceAllocation records
    if wifi_allocations:
        assignment_refs = {
            did: (
                (await db.execute(
                    select(ResourcePool).where(
                        ResourcePool.id == wifi_allocations[did]["pool_id"]
                    )
                )).scalar(),
                wifi_allocations[did],
            )
            for did in wifi_allocations
        }
        await create_allocations(db, assignment_refs, job_device_pairs)

    await db.commit()
    await db.refresh(pr)

    logger.info(
        "dispatched_plan plan=%d plan_run=%d devices=%d jobs=%d type=%s",
        plan_id, pr.id, len(device_ids), len(device_ids), run_type,
    )
    return pr
