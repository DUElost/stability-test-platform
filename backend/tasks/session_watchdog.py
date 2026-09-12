"""Session watchdog — consolidated background task for session lifecycle.

Handles:
  1. Host heartbeat timeout  → mark OFFLINE, jobs → UNKNOWN (lease stays ACTIVE)

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
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from backend.core.database import AsyncSessionLocal
from backend.core.job_timeout_config import HOST_HEARTBEAT_TIMEOUT_SECONDS
from backend.core.metrics import host_heartbeat_missed
from backend.models.enums import HostStatus, JobStatus
from backend.models.host import Host
from backend.models.job import JobInstance
from backend.services.state_machine import InvalidTransitionError, JobStateMachine

logger = logging.getLogger(__name__)


async def _check_host_heartbeat_timeouts(db) -> tuple[int, int]:
    """Mark hosts OFFLINE if heartbeat exceeded, transition RUNNING jobs → UNKNOWN.

    Returns (hosts_marked_offline, jobs_transitioned).
    """
    threshold = datetime.now(timezone.utc) - timedelta(
        seconds=HOST_HEARTBEAT_TIMEOUT_SECONDS,
    )
    dead_hosts = (await db.execute(
        select(Host).where(
            Host.last_heartbeat < threshold,
            Host.status == HostStatus.ONLINE.value,
        )
    )).scalars().all()

    hosts_offline = 0
    affected_jobs = 0
    for host in dead_hosts:
        running_jobs = (await db.execute(
            select(JobInstance).where(
                JobInstance.host_id == host.id,
                JobInstance.status == JobStatus.RUNNING.value,
            )
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
                locked.ended_at = datetime.now(timezone.utc)
                affected_jobs += 1
            except InvalidTransitionError:
                pass

        host.status = HostStatus.OFFLINE.value
        hosts_offline += 1
        # #1258：心跳超时事件的生产者（仪表板 Heartbeat Timeouts 面板依赖）
        host_heartbeat_missed.labels(host_id=str(host.id)).inc()
        logger.warning(
            "watchdog_host_timeout: host=%s jobs_to_unknown=%d", host.id, len(running_jobs),
        )

    return hosts_offline, affected_jobs


async def session_watchdog_once() -> None:
    """Run all watchdog checks in a single pass."""
    async with AsyncSessionLocal() as db:
        hosts_offline, jobs_unknown = await _check_host_heartbeat_timeouts(db)

        has_changes = hosts_offline or jobs_unknown
        if has_changes:
            await db.commit()
            logger.info(
                "watchdog_pass: hosts_offline=%d jobs_unknown=%d",
                hosts_offline, jobs_unknown,
            )
