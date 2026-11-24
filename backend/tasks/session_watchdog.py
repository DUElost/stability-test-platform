"""Session watchdog — consolidated background task for session lifecycle.

Handles:
  1. Host heartbeat timeout → mark OFFLINE, jobs → UNKNOWN (lease stays ACTIVE),
     and idempotently cascade associated devices → OFFLINE (including NULL
     heartbeat, expired DEGRADED, and pre-existing OFFLINE hosts with stale
     non-OFFLINE device rows; #3325 / #4117 B1).

UNKNOWN grace → FAILED and lease release are owned solely by
``device_lease_reconciler`` (#515).

Device lock expiration is handled by Reconciler
(backend.scheduler.device_lease_reconciler), the sole handler of lease
expiration since ADR-0019 Phase 4b.

Entry point: ``session_watchdog_once()`` is invoked by APScheduler
IntervalTrigger (see ``app_scheduler.py``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, exists, or_, select, update

from backend.core.database import AsyncSessionLocal
from backend.core.job_timeout_config import HOST_HEARTBEAT_TIMEOUT_SECONDS  # noqa: F401
from backend.core.metrics import host_heartbeat_missed
from backend.models.enums import HostStatus, JobStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.services.inventory_status import (
    effective_host_status,
    host_heartbeat_expired_condition,
)
from backend.services.state_machine import InvalidTransitionError, JobStateMachine

logger = logging.getLogger(__name__)


async def _reconcile_host_heartbeat_timeouts(
    db,
    *,
    now: Optional[datetime] = None,
) -> tuple[int, int, int]:
    """Mark expired/NULL-heartbeat hosts OFFLINE, transition RUNNING jobs → UNKNOWN,
    and idempotently reconcile non-OFFLINE devices on offline hosts.

    Returns ``(hosts_marked_offline, jobs_transitioned, devices_marked_offline)``.
    """
    scan_now = now or datetime.now(timezone.utc)
    expired_hb = host_heartbeat_expired_condition(now=scan_now)
    has_non_offline_device = exists(
        select(1).where(
            Device.host_id == Host.id,
            Device.status != "OFFLINE",
        )
    )

    # #2901（锁序家族 #2635/#2787/#2796/#2871 的第四处）：下方循环体逐行取 host 与
    # job 行锁，取锁顺序 = 本查询的返回序。**必须按 id 全序**——对侧 `agent_lease_extend.py`
    # 是 `ORDER BY JobInstance.id`，无保证的返回序与之交错即可成环。
    candidate_host_ids = (await db.execute(
        select(Host.id)
        .where(
            or_(
                and_(
                    Host.status != HostStatus.OFFLINE.value,
                    expired_hb,
                ),
                and_(
                    Host.status == HostStatus.OFFLINE.value,
                    has_non_offline_device,
                ),
            )
        )
        .order_by(Host.id)
    )).scalars().all()

    hosts_offline = 0
    affected_jobs = 0
    devices_offline = 0

    for host_id in candidate_host_ids:
        # 落库前锁行复读并复验心跳：防 watchdog 与心跳恢复并发时的 lost update。
        locked_host = (await db.execute(
            select(Host)
            .where(Host.id == host_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )).scalars().first()
        if locked_host is None:
            continue

        verify_now = now or datetime.now(timezone.utc)
        if effective_host_status(locked_host, now=verify_now) != HostStatus.OFFLINE.value:
            continue

        host_newly_offline = locked_host.status != HostStatus.OFFLINE.value
        jobs_this_host = 0
        if host_newly_offline:
            running_jobs = (await db.execute(
                select(JobInstance)
                .where(
                    JobInstance.host_id == locked_host.id,
                    JobInstance.status == JobStatus.RUNNING.value,
                )
                # 同上：job 行锁按 id 升序取（与 extend/complete/reconciler 同一全序）
                .order_by(JobInstance.id)
            )).scalars().all()

            for job in running_jobs:
                # #792: 行锁复读 + 状态复查——watchdog 与 /complete 并发时，
                # 陈旧 ORM 对象会把已提交终态覆写回 UNKNOWN（全仓唯一裸写终态
                # 路径，lost update）。populate_existing 保证读到锁后最新行；
                # 已非 RUNNING 即跳过。
                locked = (await db.execute(
                    select(JobInstance)
                    .where(JobInstance.id == job.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )).scalars().first()
                if locked is None or locked.status != JobStatus.RUNNING.value:
                    continue
                try:
                    JobStateMachine.transition(
                        locked, JobStatus.UNKNOWN, "host_heartbeat_timeout",
                    )
                    locked.ended_at = verify_now
                    affected_jobs += 1
                    jobs_this_host += 1
                except InvalidTransitionError:
                    pass

            locked_host.status = HostStatus.OFFLINE.value
            hosts_offline += 1
            # #1258：心跳超时事件的生产者（仪表板 Heartbeat Timeouts 面板依赖）
            host_heartbeat_missed.labels(host_id=str(locked_host.id)).inc()

        # #3325 / #4117 B1：幂等级联关联设备为 OFFLINE（只改 status 列，
        # 不改写最后观测的 ADB 字段、last_seen、租约或退役事实；无主设备
        # host_id IS NULL 不会命中本条件）。
        dev_res = await db.execute(
            update(Device)
            .where(
                Device.host_id == locked_host.id,
                Device.status != "OFFLINE",
            )
            .values(status="OFFLINE")
        )
        dev_count = int(dev_res.rowcount or 0)
        devices_offline += dev_count

        if host_newly_offline:
            logger.warning(
                "watchdog_host_timeout: host=%s jobs_to_unknown=%d devices_to_offline=%d",
                locked_host.id, jobs_this_host, dev_count,
            )
        elif dev_count > 0:
            logger.info(
                "watchdog_offline_host_devices_reconciled: host=%s devices_to_offline=%d",
                locked_host.id, dev_count,
            )

    return hosts_offline, affected_jobs, devices_offline


async def _check_host_heartbeat_timeouts(
    db,
    *,
    now: Optional[datetime] = None,
) -> tuple[int, int]:
    """向后兼容 2-tuple 入口：返回 ``(hosts_marked_offline, jobs_transitioned)``。"""
    hosts_offline, affected_jobs, _ = await _reconcile_host_heartbeat_timeouts(
        db, now=now,
    )
    return hosts_offline, affected_jobs


async def session_watchdog_once(*, now: Optional[datetime] = None) -> None:
    """Run all watchdog checks in a single pass."""
    async with AsyncSessionLocal() as db:
        hosts_offline, jobs_unknown, devices_offline = (
            await _reconcile_host_heartbeat_timeouts(db, now=now)
        )

        has_changes = hosts_offline or jobs_unknown or devices_offline
        if has_changes:
            await db.commit()
            logger.info(
                "watchdog_pass: hosts_offline=%d jobs_unknown=%d devices_offline=%d",
                hosts_offline, jobs_unknown, devices_offline,
            )
