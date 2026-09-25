"""Agent Job 认领（#1520 垂直切片：agent_api claim）。

``POST /jobs/claim``：版本门禁 → 主机锁/退役/维护 → 空闲设备 PENDING 认领 →
lease 获取 → JobOut 装配（serial / watcher_policy / PlanRunHost）。
路由退化为 ``ok(await claim_agent_jobs(...))``。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.metrics import claim_lease_failed_total
from backend.models.device_lease import DeviceLease
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun, PlanRunHost
from backend.services.errors import UpgradeRequired
from backend.services.host_maintenance import in_maintenance_window
from backend.services.lease_manager import acquire_lease
from backend.services.plan_dispatcher_core import (
    apply_dispatch_host_watcher_admin_state_to_policy,
    extract_dispatch_host_watcher_admin_states,
)
from backend.services.state_machine import InvalidTransitionError, JobStateMachine

logger = logging.getLogger(__name__)


class LockAcquireFailed(Exception):
    """Raised inside a savepoint when device lock acquire fails."""


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso_or_none(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return _as_utc(dt).isoformat()


class ClaimRequest(BaseModel):
    host_id: str
    capacity: int = 10
    agent_instance_id: str = ""   # ADR-0019 Phase 3a
    agent_version: str = ""


class JobOut(BaseModel):
    id: int
    plan_run_id: Optional[int] = None
    plan_id: Optional[int] = None
    device_id: int
    device_serial: Optional[str] = None
    host_id: Optional[str]
    status: str
    pipeline_def: dict
    # Watcher 策略覆盖（来自 Plan.watcher_policy）
    # Agent 解析见 backend/agent/watcher/policy.py WatcherPolicy.from_job
    watcher_policy: Optional[Dict[str, Any]] = None
    fencing_token: str  # ADR-0019 Phase 2b: 必填，来自 DeviceLease.fencing_token
    # ADR-0026 Step 5b: the PlanRunHost row id the Agent's Coordinator needs
    # to identify which PlanRunHost projection this job belongs to.
    plan_run_host_id: Optional[int] = None
    # ADR-0026 barrier: expected peer count on this PlanRunHost (INIT→PATROL).
    # Prefer total_job_count; fall back to device_count at claim time.
    plan_run_host_total_job_count: Optional[int] = None
    # claim 时写入的 job.started_at；Agent 用于派生 AEE run_date_stamp。
    started_at: Optional[str] = None


async def enrich_job_metadata(
    db: AsyncSession, jobs: List[JobInstance],
) -> tuple[Dict[int, str], Dict[int, Optional[Dict[str, Any]]]]:
    """批量获取 JobOut 需要的 device_serial + watcher_policy。

    返回：
        serial_map:         device_id -> serial
        watcher_policy_map: job_id    -> watcher_policy (来自 PlanRun.plan_snapshot)
    空 jobs 返回 ({}, {})，避免空集合上的 IN ()（PostgreSQL 会报语法错误）。
    """
    if not jobs:
        return {}, {}

    device_ids = [j.device_id for j in jobs]
    serial_rows = await db.execute(
        select(Device.id, Device.serial).where(Device.id.in_(device_ids))
    )
    serial_map = {row.id: row.serial for row in serial_rows.all()}

    plan_run_ids = {j.plan_run_id for j in jobs if j.plan_run_id is not None}
    watcher_admin_snapshot_by_run: Dict[int, Dict[str, bool]] = {}
    watcher_policy_by_run: Dict[int, Optional[dict]] = {}
    if plan_run_ids:
        snapshot_rows = await db.execute(
            select(
                PlanRun.id,
                PlanRun.run_context,
                PlanRun.plan_snapshot,
            ).where(PlanRun.id.in_(plan_run_ids))
        )
        for row in snapshot_rows.all():
            watcher_admin_snapshot_by_run[row.id] = (
                extract_dispatch_host_watcher_admin_states(row.run_context)
            )
            snapshot_plan = (
                (row.plan_snapshot or {}).get("plan", {})
                if isinstance(row.plan_snapshot, dict)
                else {}
            )
            watcher_policy_by_run[row.id] = snapshot_plan.get("watcher_policy")

    watcher_policy_map = {
        j.id: apply_dispatch_host_watcher_admin_state_to_policy(
            watcher_policy_by_run.get(j.plan_run_id)
            if j.plan_run_id is not None
            else None,
            host_id=j.host_id,
            dispatch_host_watcher_admin_states=(
                watcher_admin_snapshot_by_run.get(j.plan_run_id)
                if j.plan_run_id is not None
                else None
            ),
        )
        for j in jobs
    }

    return serial_map, watcher_policy_map


async def claim_jobs_for_host(
    db: AsyncSession,
    host_id: str,
    capacity: int = 10,
    agent_instance_id: str = "",
) -> tuple[List[JobInstance], Dict[int, str]]:
    """Shared claim logic for ``claim_jobs`` (POST).

    Phase 2d hardening:
    - Host row FOR UPDATE serializes concurrent claims for the same host
    - Capacity comes from the Agent's reported value; real cap is the free healthy device count (93b9935 removed the host slot limit)
    - Non-expired ACTIVE leases (JOB/Script/MAINTENANCE) pre-filter busy devices
    - row_number() per device picks the earliest PENDING job
    - FOR UPDATE OF JobInstance SKIP LOCKED prevents thundering herd
    - Unified exit: commit on success, rollback on empty to release host lock

    Returns (claimed_jobs, fencing_token_map).
    """
    now = datetime.now(timezone.utc)

    # 1. Lock host row — serializes concurrent claims for the same host
    host_row = (await db.execute(
        select(Host).where(Host.id == host_id).with_for_update()
    )).scalars().first()
    if not host_row:
        return [], {}  # host not found, no lock acquired — safe early return
    if host_row.status != HostStatus.ONLINE.value:
        await db.rollback()
        return [], {}
    # #1805 ④（claim 切片）：退役主机（`retired_at IS NOT NULL`）不认领——与派发侧
    # `_FATAL_DISPATCH_REASONS` 的 `host_retired` 同一判据（ADR-0038 D2/D5bis）。
    #
    # 为什么**显式**判 `retired_at` 而不依赖上面的 status 检查：退役**不改写 status**
    # （D1，status 归心跳所有），故退役主机仍可能是 ONLINE——只靠 status 会漏判。
    # 而若退役主机恰好离线，status 分支又会先短路，使「因退役而跳过」这一可区分信号
    # 落不下来（对照 `claim_skipped_host_maintenance` 的先例）。
    #
    # 活读 `retired_at`（不缓存）：退役可发生在准入与认领之间，认领侧必须看到最新值，
    # 否则「检查完 → 退役 → 认领」窗口内仍会派作业给退役机。
    if host_row.retired_at is not None:
        logger.info("claim_skipped_host_retired host=%s", host_id)
        await db.rollback()
        return [], {}
    # #960：主机在维护窗口内（热更新上传/重启中）不认领 —— 与派发侧同一判据，
    # 否则「检查完活跃 Job → 重启」之间仍会认领到新作业并被重启打断。
    if in_maintenance_window(host_row.maintenance_until, now=now):
        logger.info("claim_skipped_host_maintenance host=%s", host_id)
        await db.rollback()
        return [], {}

    # 2. Effective capacity — each device runs at most 1 Job;
    #    Agent's "capacity" is the soft cap on concurrent jobs.
    #    Defensive clamp: never claim more than free device count.
    effective_capacity = capacity

    # 4. Get all device IDs for this host (Phase 3c: filter known-unhealthy devices)
    #    - INCLUDE: known-healthy OR never-reported (NULL adb fields → coalesce to safe default)
    #    - EXCLUDE: known-offline (adb_connected=False, bad adb_state, status=OFFLINE)
    device_ids_result = await db.execute(
        select(Device.id).where(
            Device.host_id == host_id,
            func.coalesce(Device.adb_connected, True) == True,
            func.coalesce(Device.adb_state, "device").notin_(["offline", "unknown"]),
            Device.status != "OFFLINE",
        )
    )
    all_device_ids = [row[0] for row in device_ids_result.all()]

    # 5. Pre-filter: exclude devices with ANY ACTIVE lease (Phase 4b blocking lease).
    #    Expired (grace-held) ACTIVE leases also block the device —
    #    only Reconciler can release them.
    free_device_ids: list[int] = []
    if all_device_ids:
        busy_rows = await db.execute(
            select(DeviceLease.device_id).where(
                DeviceLease.device_id.in_(all_device_ids),
                DeviceLease.status == LeaseStatus.ACTIVE.value,
            )
        )
        busy_device_ids = {row[0] for row in busy_rows.all()}
        free_device_ids = [did for did in all_device_ids if did not in busy_device_ids]

    effective_capacity = min(effective_capacity, len(free_device_ids))

    # 6. Per-device first PENDING job + FOR UPDATE SKIP LOCKED
    pending_jobs: list[JobInstance] = []
    claimed: list[JobInstance] = []
    claimed_device_ids: set[int] = set()
    fencing_token_map: Dict[int, str] = {}

    if effective_capacity > 0 and free_device_ids:
        rn = func.row_number().over(
            partition_by=JobInstance.device_id,
            order_by=(JobInstance.created_at, JobInstance.id),
        ).label("rn")

        ranked = (
            select(JobInstance.id, rn)
            .join(PlanRun, PlanRun.id == JobInstance.plan_run_id)
            .where(
                JobInstance.device_id.in_(free_device_ids),
                JobInstance.status == JobStatus.PENDING.value,
                PlanRun.status == "RUNNING",
            )
        ).subquery("ranked")

        pending_jobs = (await db.execute(
            select(JobInstance)
            .join(ranked, JobInstance.id == ranked.c.id)
            .where(ranked.c.rn == 1)
            .order_by(JobInstance.created_at)
            .limit(effective_capacity)
            .with_for_update(of=JobInstance.__table__, skip_locked=True)
        )).scalars().all()

    # 7-8. Claim loop
    for job in pending_jobs:
        if job.device_id in claimed_device_ids:
            continue

        try:
            async with db.begin_nested():
                JobStateMachine.transition(job, JobStatus.RUNNING, "claimed_by_agent")
                job.host_id = host_id
                job.started_at = now

                lease = await acquire_lease(
                    db,
                    device_id=job.device_id,
                    host_id=host_id,
                    lease_type=LeaseType.JOB,
                    agent_instance_id=agent_instance_id,
                    job_id=job.id,
                )
                if lease is None:
                    claim_lease_failed_total.inc()
                    raise LockAcquireFailed()
                fencing_token_map[job.id] = lease.fencing_token

            claimed.append(job)
            claimed_device_ids.add(job.device_id)
        except LockAcquireFailed:
            continue
        except InvalidTransitionError:
            continue

    # 9. Unified exit: commit to release host FOR UPDATE lock
    if claimed:
        await db.commit()
    else:
        await db.rollback()

    return claimed, fencing_token_map


async def claim_agent_jobs(
    db: AsyncSession,
    payload: ClaimRequest,
) -> List[JobOut]:
    """Find PENDING jobs for devices on this host, claim up to `capacity`.

    Per-device deduplication: only one job per device is claimed per call.
    Uses device_leases as the sole conflict source (Phase 2c).
    Response includes device_serial + watcher_policy for Agent JobSession boot.
    """
    from backend.services.agent_version_gate import (
        agent_version_is_supported,
        resolve_agent_min_version,
    )

    minimum_version = resolve_agent_min_version()
    if minimum_version and not agent_version_is_supported(
        payload.agent_version, minimum_version,
    ):
        raise UpgradeRequired({
            "code": "AGENT_UPGRADE_REQUIRED",
            "agent_version": payload.agent_version,
            "minimum_version": minimum_version,
        })

    claimed, fencing_token_map = await claim_jobs_for_host(
        db, payload.host_id, payload.capacity, payload.agent_instance_id,
    )

    if not claimed:
        return []

    # Enrich response: device_serial + watcher_policy(from Plan)
    serial_map, watcher_policy_map = await enrich_job_metadata(db, claimed)

    # ADR-0026 Step 5b: resolve PlanRunHost rows so the Agent's Coordinator
    # knows which host-group projection to update.
    prh_by_key: Dict[tuple[int, str], tuple[int, int]] = {}
    plan_run_ids = {j.plan_run_id for j in claimed if j.plan_run_id}
    if plan_run_ids:
        prh_rows = (await db.execute(
            select(
                PlanRunHost.plan_run_id,
                PlanRunHost.host_id,
                PlanRunHost.id,
                PlanRunHost.total_job_count,
                PlanRunHost.device_count,
            )
            .where(
                PlanRunHost.plan_run_id.in_(plan_run_ids),
                PlanRunHost.host_id == payload.host_id,
            )
        )).all()
        prh_by_key = {
            (r.plan_run_id, r.host_id): (
                r.id,
                int(r.total_job_count or r.device_count or 0),
            )
            for r in prh_rows
        }

    out: list = []
    for j in claimed:
        prh_info = prh_by_key.get((j.plan_run_id, j.host_id))
        out.append(
            JobOut(
                id=j.id, plan_run_id=j.plan_run_id,
                plan_id=j.plan_id, device_id=j.device_id,
                device_serial=serial_map.get(j.device_id),
                host_id=j.host_id, status=j.status, pipeline_def=j.pipeline_def,
                watcher_policy=watcher_policy_map.get(j.id),
                fencing_token=fencing_token_map[j.id],
                plan_run_host_id=prh_info[0] if prh_info else None,
                plan_run_host_total_job_count=prh_info[1] if prh_info else None,
                started_at=_iso_or_none(j.started_at),
            )
        )
    return out

# 路由 / 既有测试用的私有名别名。
_enrich_job_metadata = enrich_job_metadata
_claim_jobs_for_host = claim_jobs_for_host
_LockAcquireFailed = LockAcquireFailed
