"""Plan dispatcher (sync) — ADR-0020.

Sync counterpart of ``plan_dispatcher.py`` for use with sync FastAPI endpoints.
"""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, and_, func
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.core.pipeline_validator import validate_pipeline_def
from backend.models.device_lease import DeviceLease
from backend.models.enums import DeviceStatus, HostStatus, JobStatus, LeaseStatus, PlanRunStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.models.resource_pool import ResourceAllocation, ResourcePool
from backend.models.script import Script
from backend.services.plan_dispatcher_core import (
    PlanDispatchError,
    build_lifecycle_from_steps as _build_lifecycle_from_steps,
    build_lifecycle_from_snapshot as _build_lifecycle_from_snapshot,
    build_plan_snapshot as _build_plan_snapshot,
    build_preview as _build_preview,
    check_legacy_aee_script_refs as _check_legacy_aee_script_refs,
    check_script_keys_complete as _check_script_keys_complete,
    inject_wifi_params as _inject_wifi_params,
    iter_lifecycle_steps as _iter_lifecycle_steps,
    script_defaults as _script_defaults,
    snapshot_dispatch_host_watcher_admin_states,
)
from backend.services.state_machine import PlanRunStateMachine

logger = logging.getLogger(__name__)

ACTIVE_JOB_STATUSES = (
    JobStatus.PENDING.value,
    JobStatus.RUNNING.value,
    JobStatus.UNKNOWN.value,
)



def _validate_dispatch_devices_sync(
    db: Session, device_ids: list[int]
) -> None:
    """Strict device availability check for dispatch.

    Why: 此前 dispatcher 对不存在/OFFLINE/已被 ACTIVE lease 占用的设备只 WARNING 后
         继续创建 host_id=None 或永远 PENDING 的 job,用户体感"派发成功但永不跑"。
         严格全拒 + 结构化 detail,前端拿到每台设备的具体原因。

    判定优先级(短路返回第一个不通过原因):
      - not_found:device_id 不存在
      - no_host:Device.host_id 为 NULL
      - device_offline:Device.status == OFFLINE
      - host_offline:Host.status == OFFLINE
      - active_lease:存在 DeviceLease.status == ACTIVE 行(lease 是占用真值)

    Raises:
        PlanDispatchError(unavailable_devices=[{id, reason, ...}])
    """
    if not device_ids:
        raise PlanDispatchError("device_ids must not be empty")

    rows = db.execute(
        select(
            Device.id,
            Device.host_id,
            Device.status.label("device_status"),
            Host.status.label("host_status"),
        )
        .select_from(Device)
        .outerjoin(Host, Device.host_id == Host.id)
        .where(Device.id.in_(device_ids))
    ).all()
    device_snapshot = {row.id: row for row in rows}

    lease_rows = db.execute(
        select(DeviceLease.device_id, DeviceLease.job_id)
        .where(
            DeviceLease.device_id.in_(device_ids),
            DeviceLease.status == LeaseStatus.ACTIVE.value,
        )
    ).all()
    active_lease_by_device = {row.device_id: row.job_id for row in lease_rows}

    # B4: the partial unique index ``uq_job_active_per_device`` (job.py) forbids a
    # second job on a device while one is PENDING/RUNNING/UNKNOWN. The ACTIVE-lease
    # check above only catches RUNNING (leases exist post-claim); a PENDING job
    # from another PlanRun holds no lease yet still occupies the index, so without
    # this check dispatch validation passes and materialization later dies on an
    # IntegrityError, leaving the PlanRun hung. Query the exact index predicate so
    # validation and materialization share one source of truth.
    active_job_rows = db.execute(
        select(JobInstance.device_id, JobInstance.id, JobInstance.status)
        .where(
            JobInstance.device_id.in_(device_ids),
            JobInstance.status.in_(ACTIVE_JOB_STATUSES),
        )
        .order_by(JobInstance.id)
    ).all()
    active_job_by_device: dict[int, tuple[int, str]] = {}
    for row in active_job_rows:
        active_job_by_device.setdefault(row.device_id, (row.id, row.status))

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
        if snap.device_status == DeviceStatus.ERROR.value:
            unavailable.append({
                "id": did, "reason": "device_error",
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
        if did in active_job_by_device:
            # PENDING/UNKNOWN active job with no lease yet (RUNNING is already
            # caught by active_lease above). Same index conflict, distinct reason
            # so the caller/UI can tell "queued elsewhere" from "running elsewhere".
            conflict_job_id, conflict_status = active_job_by_device[did]
            unavailable.append({
                "id": did, "reason": "active_job",
                "job_id": conflict_job_id,
                "job_status": conflict_status,
            })
            continue

    if unavailable:
        raise PlanDispatchError(
            f"Dispatch rejected: {len(unavailable)} device(s) unavailable",
            unavailable_devices=unavailable,
        )


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
        .order_by(ResourcePool.id)
        .with_for_update()
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

    disabled = _check_legacy_aee_script_refs(steps)
    if disabled:
        raise PlanDispatchError(
            f"Plan {plan_id}: legacy AEE scripts disabled at preview: {', '.join(disabled)}",
            disabled_legacy_scripts=disabled,
        )

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


def initial_dispatch_state() -> dict:
    """Seed ``run_context.dispatch_state`` for gate-backed dispatch paths."""
    return {
        "enqueue_key": None,
        "requeue_attempts": 0,
        "status": "queued",
        "enqueued_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
        "started_at": None,
        "completed_at": None,
        "last_error": None,
    }


def _run_dispatch_gate_sync(plan_run_id: int, db: Session) -> None:
    """Run the ADR-0021 dispatch gate inline (CHAIN / SCHEDULE sync paths)."""
    from backend.services.plan_precheck import _drive_dispatch_gate

    asyncio.run(_drive_dispatch_gate(plan_run_id, db=db))


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
    commit: bool = True,
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

    # #8: device 可用性快照校验 — 早返回避免创建落空 PlanRun。complete 阶段会再做一次
    # TOCTOU 兜底,但用户期望的是 400,不是落 FAILED 的 PlanRun。
    _validate_dispatch_devices_sync(db, device_ids)

    steps = db.execute(
        select(PlanStep)
        .where(PlanStep.plan_id == plan_id)
        .order_by(PlanStep.stage, PlanStep.sort_order)
    ).scalars().all()
    if not steps:
        raise PlanDispatchError(f"Plan {plan_id} has no steps")

    disabled = _check_legacy_aee_script_refs(steps)
    if disabled:
        raise PlanDispatchError(
            f"Plan {plan_id}: legacy AEE scripts disabled at prepare: {', '.join(disabled)}",
            disabled_legacy_scripts=disabled,
        )

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
    merged_run_ctx["dispatch_host_watcher_admin_states"] = (
        snapshot_dispatch_host_watcher_admin_states(db, device_ids)
    )

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

    if commit:
        db.commit()
        db.refresh(pr)
    else:
        db.flush()
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
    pr = db.execute(
        select(PlanRun)
        .where(PlanRun.id == plan_run_id)
        .with_for_update(key_share=True)
    ).scalar_one_or_none()
    if pr is None:
        raise PlanDispatchError(f"PlanRun {plan_run_id} not found")
    run_ctx = dict(pr.run_context or {})
    if pr.status != PlanRunStatus.RUNNING.value or run_ctx.get("abort_requested"):
        logger.info(
            "complete_plan_run_dispatch_skip plan_run=%d status=%s abort=%s",
            plan_run_id,
            pr.status,
            bool(run_ctx.get("abort_requested")),
        )
        return

    existing_jobs = db.execute(
        select(JobInstance.id).where(JobInstance.plan_run_id == plan_run_id).limit(1)
    ).first()
    if existing_jobs is not None:
        logger.info(
            "complete_plan_run_dispatch_skip plan_run=%d (jobs already created)",
            plan_run_id,
        )
        return

    # Stage 2 is isolated from later Plan/PlanStep edits.  Script availability
    # and content integrity were verified against this same snapshot by the
    # dispatch gate; materialization must not silently switch to live rows.
    lifecycle = _build_lifecycle_from_snapshot(pr.plan_snapshot)
    if not any(True for _ in _iter_lifecycle_steps({"lifecycle": lifecycle})):
        raise PlanDispatchError(f"PlanRun {plan_run_id} snapshot has no enabled steps")

    device_ids = list(run_ctx.get("dispatch_device_ids") or [])
    if not device_ids:
        raise PlanDispatchError(
            f"PlanRun {plan_run_id}: run_context.dispatch_device_ids is empty"
        )

    # #8 TOCTOU 兜底:prepare→complete 之间设备可能下线/被占用。此时 PlanRun 行已存在
    # 且前端正在观测,不能 raise;转 FAILED 终态 + 审计,与 ADR-0023 C1 同处理路径。
    try:
        _validate_dispatch_devices_sync(db, device_ids)
    except PlanDispatchError as exc:
        from backend.core.audit import record_audit
        unavailable = exc.unavailable_devices or []
        PlanRunStateMachine.transition(pr, PlanRunStatus.FAILED, reason="devices_unavailable_at_dispatch")
        pr.ended_at = datetime.now(timezone.utc)
        pr.result_summary = {
            "dispatch_failed": True,
            "unavailable_devices": unavailable,
        }
        flag_modified(pr, "result_summary")
        record_audit(
            db,
            action="plan_dispatch_failed",
            resource_type="plan_run",
            resource_id=pr.id,
            details={
                "unavailable_devices": unavailable,
                "reason": "devices_unavailable_at_dispatch",
            },
        )
        db.commit()
        logger.warning(
            "plan_dispatch_failed_devices_unavailable plan_run=%d devices=%s",
            plan_run_id, unavailable,
        )
        return

    device_rows = db.execute(
        select(Device.id, Device.host_id).where(Device.id.in_(device_ids))
    ).all()
    device_host_map = {row.id: row.host_id for row in device_rows}

    orphan_devices = [did for did in device_ids if not device_host_map.get(did)]
    if orphan_devices:
        # Why: prepare 阶段 _validate_dispatch_devices_sync 已覆盖 no_host;
        # 若仍走到这里说明 complete 前并发 race(host_id 被改为 NULL) — 必须 FAILED 而非静默 WARN。
        from backend.core.audit import record_audit
        PlanRunStateMachine.transition(pr, PlanRunStatus.FAILED, reason="devices_without_host")
        pr.ended_at = datetime.now(timezone.utc)
        pr.result_summary = {
            "dispatch_failed": True,
            "reason": "devices_without_host",
            "orphan_device_ids": orphan_devices,
        }
        flag_modified(pr, "result_summary")
        record_audit(
            db,
            action="plan_dispatch_failed",
            resource_type="plan_run",
            resource_id=pr.id,
            details={
                "reason": "devices_without_host",
                "orphan_device_ids": orphan_devices,
            },
        )
        db.commit()
        logger.warning(
            "plan_dispatch_failed_devices_without_host plan_run=%d device_ids=%s",
            plan_run_id, orphan_devices,
        )
        return

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
            from backend.core.audit import record_audit
            PlanRunStateMachine.transition(pr, PlanRunStatus.FAILED, reason="wifi_allocation_failed")
            pr.ended_at = datetime.now(timezone.utc)
            pr.result_summary = {
                "dispatch_failed": True,
                "reason": "wifi_allocation_failed",
                "error": str(exc),
            }
            flag_modified(pr, "result_summary")
            record_audit(
                db,
                action="plan_dispatch_failed",
                resource_type="plan_run",
                resource_id=pr.id,
                details={"reason": "wifi_allocation_failed", "error": str(exc)},
            )
            db.commit()
            logger.warning(
                "plan_dispatch_wifi_allocation_failed plan_run=%d error=%s",
                plan_run_id, exc,
            )
            return

    now = datetime.now(timezone.utc)
    job_device_pairs: dict[int, int] = {}

    for device_id in device_ids:
        wifi_params = wifi_allocations.get(device_id)
        resolved_pipeline = {"lifecycle": deepcopy(lifecycle)}
        if wifi_params:
            resolved_pipeline = _inject_wifi_params(resolved_pipeline, wifi_params)

        job = JobInstance(
            plan_run_id=pr.id,
            plan_id=pr.plan_id,
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
    """ADR-0020/0021 — Sync dispatch via the dispatch gate (verify → sync → dispatch).

    Used by SCHEDULE (cron) and CHAIN trigger paths.  Runs the gate inline so
    callers observe a fully materialised PlanRun (or a raised
    :class:`PlanDispatchError` on gate failure).  MANUAL goes through
    :func:`prepare_plan_run` + the SAQ ``precheck_and_dispatch`` task instead.
    """
    merged_run_ctx = dict(run_context or {})
    if not merged_run_ctx.get("dispatch_state"):
        merged_run_ctx["dispatch_state"] = initial_dispatch_state()

    pr = prepare_plan_run(
        plan_id=plan_id,
        device_ids=device_ids,
        triggered_by=triggered_by,
        db=db,
        run_type=run_type,
        run_context=merged_run_ctx,
        parent_plan_run_id=parent_plan_run_id,
        root_plan_run_id=root_plan_run_id,
        chain_index=chain_index,
    )
    _run_dispatch_gate_sync(pr.id, db)
    db.refresh(pr)

    if pr.status == "FAILED":
        summary = pr.result_summary or {}
        reason = summary.get("reason") or "dispatch_gate_failed"
        raise PlanDispatchError(
            f"PlanRun {pr.id}: dispatch gate failed: {reason}",
            missing_scripts=summary.get("missing_scripts"),
            mixed_watcher_inactive_host_ids=summary.get("inactive_host_ids"),
        )

    return pr


class AllocationError(Exception):
    pass
