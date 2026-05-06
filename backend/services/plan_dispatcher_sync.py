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

from backend.core.pipeline_validator import validate_pipeline_def
from backend.models.enums import JobStatus
from backend.models.host import Device
from backend.models.job import JobInstance
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.models.resource_pool import ResourceAllocation, ResourcePool
from backend.models.script import Script

logger = logging.getLogger(__name__)

ACTIVE_JOB_STATUSES = (JobStatus.PENDING.value, JobStatus.RUNNING.value)


class PlanDispatchError(Exception):
    pass


# ── Pure helpers (shared with async version) ──────────────────────────────

def _build_lifecycle_from_steps(
    plan: Plan, steps: list[PlanStep], script_defaults: dict[tuple[str, str], dict]
) -> dict:
    lifecycle: dict[str, Any] = {"init": [], "teardown": []}
    patrol_steps: list[dict] = []
    patrol_interval: int | None = None

    for step in sorted(steps, key=lambda s: (s.stage, s.sort_order)):
        default_params = script_defaults.get((step.script_name, step.script_version), {})
        step_def: dict[str, Any] = {
            "step_id": step.step_key,
            "action": f"script:{step.script_name}",
            "version": step.script_version,
            "params": deepcopy(default_params),
            "timeout_seconds": step.timeout_seconds,
            "retry": step.retry,
        }

        if step.stage in ("init", "teardown"):
            lifecycle[step.stage].append(step_def)
        elif step.stage == "patrol":
            patrol_steps.append(step_def)

    plan_lifecycle = (plan.lifecycle or {}) if isinstance(plan.lifecycle, dict) else {}
    patrol_config = plan_lifecycle.get("patrol")
    if isinstance(patrol_config, dict):
        patrol_interval = patrol_config.get("interval_seconds", 60)

    if patrol_steps:
        lifecycle["patrol"] = {
            "interval_seconds": patrol_interval or 60,
            "steps": patrol_steps,
        }

    plan_timeout = plan_lifecycle.get("timeout_seconds")
    if plan_timeout is not None:
        lifecycle["timeout_seconds"] = plan_timeout

    return lifecycle


def _iter_lifecycle_steps(pipeline: dict):
    lifecycle = (pipeline or {}).get("lifecycle", {})
    for phase_name in ("init", "teardown"):
        steps = lifecycle.get(phase_name)
        if isinstance(steps, list):
            for step in steps:
                yield phase_name, step
    patrol = lifecycle.get("patrol")
    if isinstance(patrol, dict) and isinstance(patrol.get("steps"), list):
        for step in patrol["steps"]:
            yield "patrol", step


def _inject_wifi_params(pipeline: dict, wifi_params: dict | None) -> dict:
    if not wifi_params or not wifi_params.get("ssid"):
        return pipeline
    for _, step in _iter_lifecycle_steps(pipeline):
        action = step.get("action", "")
        if "connect_wifi" not in action:
            continue
        params = dict(step.get("params") or {})
        if not params.get("ssid"):
            params["ssid"] = wifi_params["ssid"]
        if not params.get("password"):
            params["password"] = wifi_params.get("password", "")
        step["params"] = params
    return pipeline


def _build_preview(plan: Plan, lifecycle: dict, device_ids: list[int]) -> dict:
    steps = list(_iter_lifecycle_steps({"lifecycle": lifecycle}))
    return {
        "plan_id": plan.id,
        "plan_name": plan.name,
        "device_ids": device_ids,
        "device_count": len(device_ids),
        "job_count": len(device_ids),
        "total_steps": len(steps),
        "lifecycle": lifecycle,
    }


def _fetch_script_defaults(
    db: Session, steps: list[PlanStep]
) -> dict[tuple[str, str], dict]:
    keys = {(s.script_name, s.script_version) for s in steps}
    if not keys:
        return {}
    names = {k[0] for k in keys}
    rows = db.execute(
        select(Script.name, Script.version, Script.default_params).where(
            Script.name.in_(names), Script.is_active.is_(True)
        )
    ).all()
    return {(r.name, r.version): (r.default_params or {}) for r in rows}


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

    defaults = _fetch_script_defaults(db, steps)
    lifecycle = _build_lifecycle_from_steps(plan, steps, defaults)
    return _build_preview(plan, lifecycle, device_ids)


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
    failure_threshold_override: float | None = None,
) -> PlanRun:
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

    defaults = _fetch_script_defaults(db, steps)
    lifecycle = _build_lifecycle_from_steps(plan, steps, defaults)

    is_valid, errors = validate_pipeline_def({"lifecycle": lifecycle})
    if not is_valid:
        raise PlanDispatchError(
            f"Plan {plan_id} generated invalid lifecycle: {'; '.join(errors)}"
        )

    # Device → host mapping
    device_rows = db.execute(
        select(Device.id, Device.host_id).where(Device.id.in_(device_ids))
    ).all()
    device_host_map = {row.id: row.host_id for row in device_rows}

    orphan_devices = [did for did in device_ids if not device_host_map.get(did)]
    if orphan_devices:
        logger.warning(
            "plan_dispatch_devices_without_host: device_ids=%s", orphan_devices
        )

    # WiFi allocation (best-effort)
    wifi_allocations: dict[int, dict] = {}
    try:
        assignments = _sync_allocate_devices(db, device_ids, resource_type="wifi")
        for device_id, (_pool, alloc_params) in assignments.items():
            wifi_allocations[device_id] = alloc_params
        logger.info("plan_dispatch_wifi_allocated: devices=%d", len(device_ids))
    except AllocationError as exc:
        logger.warning("plan_dispatch_wifi_allocation_skipped: %s", exc)

    effective_threshold = (
        failure_threshold_override
        if failure_threshold_override is not None
        else plan.failure_threshold
    )

    plan_snapshot = {
        "plan_id": plan.id,
        "name": plan.name,
        "failure_threshold": effective_threshold,
        "lifecycle": lifecycle,
        "watcher_policy": plan.watcher_policy,
    }

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
        chain_index=chain_index,
        started_at=datetime.now(timezone.utc),
    )
    db.add(pr)
    db.flush()

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
        "dispatched_plan plan=%d plan_run=%d devices=%d jobs=%d type=%s",
        plan_id, pr.id, len(device_ids), len(device_ids), run_type,
    )
    return pr


class AllocationError(Exception):
    pass
