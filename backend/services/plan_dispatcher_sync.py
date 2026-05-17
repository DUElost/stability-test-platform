"""Plan dispatcher (sync) — ADR-0020.

Sync counterpart of ``plan_dispatcher.py`` for use with sync FastAPI endpoints.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, and_, func
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.core.pipeline_validator import validate_pipeline_def
from backend.models.enums import JobStatus
from backend.models.host import Device
from backend.models.job import JobInstance
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.models.resource_pool import ResourceAllocation, ResourcePool
from backend.models.script import Script
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

ACTIVE_JOB_STATUSES = (JobStatus.PENDING.value, JobStatus.RUNNING.value)


def _fetch_script_metadata(
    db: Session, steps: list[PlanStep]
) -> dict[tuple[str, str], dict[str, dict]]:
    keys = {(s.script_name, s.script_version) for s in steps}
    if not keys:
        return {}
    names = {k[0] for k in keys}
    rows = db.execute(
        select(
            Script.name,
            Script.version,
            Script.default_params,
            Script.param_schema,
            Script.nfs_path,
        ).where(
            Script.name.in_(names), Script.is_active.is_(True)
        )
    ).all()
    return {
        (r.name, r.version): {
            "default_params": r.default_params or {},
            "param_schema": r.param_schema or {},
            "nfs_path": r.nfs_path or "",
        }
        for r in rows
    }
# ── Sync resource pool helpers ────────────────────────────────────────────

def _sync_allocate_devices(
    db: Session,
    device_ids: list[int],
    resource_type: str = "wifi",
) -> dict[int, tuple[ResourcePool, dict[str, Any]]]:
    pools = db.execute(
        select(ResourcePool).where(
            ResourcePool.resource_type == resource_type,
            ResourcePool.is_active.is_(True),
        )
    ).scalars().all()

    if not pools:
        raise AllocationError(f"No active {resource_type} resource pools")

    pool_ids = [p.id for p in pools]

    # Current loads
    load_rows = db.execute(
        select(
            ResourceAllocation.resource_pool_id,
            func.count(ResourceAllocation.id),
        )
        .join(JobInstance, JobInstance.id == ResourceAllocation.job_instance_id)
        .where(
            ResourceAllocation.resource_pool_id.in_(pool_ids),
            JobInstance.status.in_(ACTIVE_JOB_STATUSES),
        )
        .group_by(ResourceAllocation.resource_pool_id)
    ).all()
    loads = {row[0]: row[1] for row in load_rows}

    allocations: dict[int, tuple[ResourcePool, dict[str, Any]]] = {}

    for device_id in device_ids:
        ordered = sorted(pools, key=lambda p: (loads.get(p.id, 0), p.id))

        chosen = None
        for pool in ordered:
            current_load = loads.get(pool.id, 0)
            if current_load >= pool.max_concurrent_devices:
                continue
            chosen = pool
            break

        if chosen is None:
            raise AllocationError(
                f"No capacity for device {device_id}: all pools full"
            )

        loads[chosen.id] = loads.get(chosen.id, 0) + 1
        config = chosen.config or {}
        allocated_params = {
            "ssid": config.get("ssid", ""),
            "password": config.get("password", ""),
            "pool_name": chosen.name,
            "pool_id": chosen.id,
        }
        allocations[device_id] = (chosen, allocated_params)

    return allocations


def _sync_create_allocations(
    db: Session,
    assignments: dict[int, tuple[ResourcePool, dict[str, Any]]],
    job_device_map: dict[int, int],
) -> list[ResourceAllocation]:
    records = []
    for job_id, device_id in job_device_map.items():
        if device_id not in assignments:
            continue
        _pool, params = assignments[device_id]
        records.append(
            ResourceAllocation(
                job_instance_id=job_id,
                resource_pool_id=_pool.id,
                device_id=device_id,
                allocated_params=params,
            )
        )
    if records:
        db.add_all(records)
        db.flush()
    return records


# ── Public API ─────────────────────────────────────────────────────────────

def preview_plan_dispatch_sync(
    plan_id: int,
    device_ids: list[int],
    db: Session,
) -> dict[str, Any]:
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise PlanDispatchError(f"Plan {plan_id} not found")

    steps = db.execute(
        select(PlanStep)
        .where(PlanStep.plan_id == plan_id)
        .order_by(PlanStep.stage, PlanStep.sort_order)
    ).scalars().all()

    if not steps:
        raise PlanDispatchError(f"Plan {plan_id} has no steps")

    metadata = _fetch_script_metadata(db, steps)
    missing = _check_script_keys_complete(steps, metadata)
    if missing:
        raise PlanDispatchError(
            f"Plan {plan_id}: scripts unavailable at preview: {', '.join(missing)}",
            missing_scripts=missing,
        )
    defaults = _script_defaults(metadata)
    lifecycle = _build_lifecycle_from_steps(plan, steps, defaults)
    return _build_preview(plan, lifecycle, device_ids)


def prepare_plan_run(
    plan_id: int,
    device_ids: list[int],
    triggered_by: str,
    db: Session,
    *,
    run_type: str = "MANUAL",
    run_context: dict | None = None,
    parent_plan_run_id: int | None = None,
    root_plan_run_id: int | None = None,
    chain_index: int | None = None,
) -> PlanRun:
    """ADR-0021 C3 — Stage 1 of dispatch: create PlanRun + plan_snapshot only.

    The dispatch gate (precheck) runs against this PlanRun, then on success
    invokes :func:`complete_plan_run_dispatch` to materialise JobInstance rows
    and resource allocations.

    ``device_ids`` is captured into ``run_context['dispatch_device_ids']`` so
    the gate can recover them without re-validating against API state.
    """
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise PlanDispatchError(f"Plan {plan_id} not found")

    steps = db.execute(
        select(PlanStep)
        .where(PlanStep.plan_id == plan_id)
        .order_by(PlanStep.stage, PlanStep.sort_order)
    ).scalars().all()
    if not steps:
        raise PlanDispatchError(f"Plan {plan_id} has no steps")

    metadata = _fetch_script_metadata(db, steps)
    missing = _check_script_keys_complete(steps, metadata)
    if missing:
        # ADR-0023 C1 阶段 1:prepare 阶段同步拒绝,不创建 PlanRun 行。
        # 端点层捕获 → HTTP 400 + {"code":"INVALID_SCRIPT_REFS","missing":[...]}
        raise PlanDispatchError(
            f"Plan {plan_id}: scripts unavailable at prepare: {', '.join(missing)}",
            missing_scripts=missing,
        )
    defaults = _script_defaults(metadata)
    lifecycle = _build_lifecycle_from_steps(plan, steps, defaults)

    is_valid, errors = validate_pipeline_def({"lifecycle": lifecycle})
    if not is_valid:
        raise PlanDispatchError(
            f"Plan {plan_id} generated invalid lifecycle: {'; '.join(errors)}"
        )

    effective_threshold = plan.failure_threshold
    plan_snapshot = _build_plan_snapshot(plan, steps, metadata, effective_threshold)

    merged_run_ctx = dict(run_context or {})
    # IH1: dispatch_device_ids is critical — plan_precheck.py L101-105 explicitly
    # asserts this list is non-empty.  Keep this line exactly as it was.
    merged_run_ctx.setdefault("dispatch_device_ids", list(device_ids))

    pr = PlanRun(
        plan_id=plan.id,
        status="RUNNING",
        failure_threshold=effective_threshold,
        plan_snapshot=plan_snapshot,
        run_type=run_type,
        run_context=merged_run_ctx,
        triggered_by=triggered_by,
        parent_plan_run_id=parent_plan_run_id,
        root_plan_run_id=root_plan_run_id,
        chain_index=chain_index or 0,
        started_at=datetime.now(timezone.utc),
    )
    db.add(pr)
    db.flush()

    # Backfill dispatch_state.enqueue_key now that plan_run.id is assigned.
    # The dispatcher gate and precheck_reaper both use this key to look up
    # the SAQ job state.  Without it, orphan detection cannot correlate
    # PlanRun rows with their SAQ precheck jobs.
    dispatch_state = merged_run_ctx.get("dispatch_state")
    if dispatch_state:
        dispatch_state["enqueue_key"] = f"precheck:{pr.id}"
        pr.run_context = {**merged_run_ctx, "dispatch_state": dispatch_state}
        flag_modified(pr, "run_context")

    db.commit()
    db.refresh(pr)
    logger.info(
        "plan_run_prepared plan=%d plan_run=%d devices=%d type=%s",
        plan_id, pr.id, len(device_ids), run_type,
    )
    return pr


def complete_plan_run_dispatch(
    plan_run_id: int,
    db: Session,
) -> None:
    """ADR-0021 C3 — Stage 2 of dispatch: materialise JobInstances + allocations.

    Reads ``plan_snapshot`` and ``run_context['dispatch_device_ids']`` from
    the PlanRun row.  Idempotent: if JobInstances already exist for this
    plan_run, returns immediately.

    Note: this is the same logic that used to live inline in
    ``dispatch_plan_sync`` after the PlanRun INSERT — relocated here so the
    dispatch gate (SAQ task) can call it independently after precheck.
    """
    pr = db.get(PlanRun, plan_run_id)
    if pr is None:
        raise PlanDispatchError(f"PlanRun {plan_run_id} not found")

    existing_jobs = db.execute(
        select(JobInstance.id).where(JobInstance.plan_run_id == plan_run_id).limit(1)
    ).first()
    if existing_jobs is not None:
        logger.info(
            "complete_plan_run_dispatch_skip plan_run=%d (jobs already created)",
            plan_run_id,
        )
        return

    plan = db.get(Plan, pr.plan_id)
    if plan is None:
        raise PlanDispatchError(f"Plan {pr.plan_id} not found")

    steps = db.execute(
        select(PlanStep)
        .where(PlanStep.plan_id == pr.plan_id)
        .order_by(PlanStep.stage, PlanStep.sort_order)
    ).scalars().all()
    if not steps:
        raise PlanDispatchError(f"Plan {pr.plan_id} has no steps")

    metadata = _fetch_script_metadata(db, steps)
    missing = _check_script_keys_complete(steps, metadata)
    if missing:
        # ADR-0023 C1 阶段 2:prepare 与 complete 之间的窗口内脚本被失活。
        # 此时 PlanRun 行已存在且前端正在观测,不能回退;转为 FAILED 终态
        # 供审计,**不 raise**——_drive_dispatch_gate 重读 status 决策。
        from backend.core.audit import record_audit
        pr.status = "FAILED"
        pr.ended_at = datetime.now(timezone.utc)
        pr.result_summary = {
            "dispatch_failed": True,
            "missing_scripts": missing,
        }
        flag_modified(pr, "result_summary")
        record_audit(
            db,
            action="plan_dispatch_failed",
            resource_type="plan_run",
            resource_id=pr.id,
            details={"missing_scripts": missing, "reason": "scripts_unavailable_at_dispatch"},
        )
        db.commit()
        logger.warning(
            "plan_dispatch_failed_missing_scripts plan_run=%d missing=%s",
            plan_run_id, missing,
        )
        return

    defaults = _script_defaults(metadata)
    lifecycle = _build_lifecycle_from_steps(plan, steps, defaults)

    run_ctx = pr.run_context or {}
    device_ids = list(run_ctx.get("dispatch_device_ids") or [])
    if not device_ids:
        raise PlanDispatchError(
            f"PlanRun {plan_run_id}: run_context.dispatch_device_ids is empty"
        )

    device_rows = db.execute(
        select(Device.id, Device.host_id).where(Device.id.in_(device_ids))
    ).all()
    device_host_map = {row.id: row.host_id for row in device_rows}

    orphan_devices = [did for did in device_ids if not device_host_map.get(did)]
    if orphan_devices:
        logger.warning(
            "plan_dispatch_devices_without_host: device_ids=%s", orphan_devices
        )

    wifi_allocations: dict[int, dict] = {}
    if any(
        "connect_wifi" in (step.get("action") or "")
        for _, step in _iter_lifecycle_steps({"lifecycle": lifecycle})
    ):
        try:
            assignments = _sync_allocate_devices(db, device_ids, resource_type="wifi")
            for device_id, (_pool, alloc_params) in assignments.items():
                wifi_allocations[device_id] = alloc_params
            logger.info("plan_dispatch_wifi_allocated: devices=%d", len(device_ids))
        except AllocationError as exc:
            logger.warning("plan_dispatch_wifi_allocation_skipped: %s", exc)

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
        db.flush()
        job_device_pairs[job.id] = device_id

    if wifi_allocations:
        assignment_refs = {
            did: (
                db.execute(
                    select(ResourcePool).where(
                        ResourcePool.id == wifi_allocations[did]["pool_id"]
                    )
                ).scalar(),
                wifi_allocations[did],
            )
            for did in wifi_allocations
        }
        _sync_create_allocations(db, assignment_refs, job_device_pairs)

    db.commit()
    db.refresh(pr)
    logger.info(
        "plan_run_dispatch_completed plan=%d plan_run=%d jobs=%d",
        pr.plan_id, pr.id, len(device_ids),
    )


def dispatch_plan_sync(
    plan_id: int,
    device_ids: list[int],
    triggered_by: str,
    db: Session,
    run_type: str = "MANUAL",
    run_context: dict | None = None,
    parent_plan_run_id: int | None = None,
    root_plan_run_id: int | None = None,
    chain_index: int | None = None,
) -> PlanRun:
    """ADR-0020 — One-shot sync dispatch (PlanRun + Jobs in one transaction).

    Used by SCHEDULE (cron) and CHAIN trigger paths which intentionally
    skip the ADR-0021 dispatch gate.  MANUAL goes through
    :func:`prepare_plan_run` + the SAQ ``precheck_and_dispatch`` task instead.
    """
    pr = prepare_plan_run(
        plan_id=plan_id,
        device_ids=device_ids,
        triggered_by=triggered_by,
        db=db,
        run_type=run_type,
        run_context=run_context,
        parent_plan_run_id=parent_plan_run_id,
        root_plan_run_id=root_plan_run_id,
        chain_index=chain_index,
    )
    complete_plan_run_dispatch(pr.id, db=db)
    db.refresh(pr)
    return pr


class AllocationError(Exception):
    pass
