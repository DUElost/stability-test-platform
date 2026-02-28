import asyncio
import logging
import os
from datetime import datetime, timedelta

from sqlalchemy import select

from backend.core.database import AsyncSessionLocal
from backend.models.enums import HostStatus, JobStatus
from backend.models.host import Host
from backend.models.job import JobInstance
from backend.services.aggregator import WorkflowAggregator
from backend.services.state_machine import JobStateMachine

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = int(os.getenv("HEARTBEAT_TIMEOUT_SECONDS", "30"))
_CHECK_INTERVAL  = int(os.getenv("HEARTBEAT_CHECK_INTERVAL_SECONDS", "10"))


async def check_heartbeat_timeouts() -> None:
    threshold = datetime.utcnow() - timedelta(seconds=_TIMEOUT_SECONDS)
    async with AsyncSessionLocal() as db:
        dead_hosts = (await db.execute(
            select(Host).where(
                Host.last_heartbeat < threshold,
                Host.status == HostStatus.ONLINE.value,
            )
        )).scalars().all()

        for host in dead_hosts:
            running_jobs = (await db.execute(
                select(JobInstance).where(
                    JobInstance.host_id == host.id,
                    JobInstance.status == JobStatus.RUNNING.value,
                )
            )).scalars().all()

            for job in running_jobs:
                JobStateMachine.transition(job, JobStatus.UNKNOWN, "host_heartbeat_timeout")
                job.ended_at = datetime.utcnow()
                await WorkflowAggregator.on_job_terminal(job, db)

            host.status = HostStatus.OFFLINE.value
            logger.warning(
                "Host %s timed out; set %d jobs to UNKNOWN", host.id, len(running_jobs)
            )

        await db.commit()


async def heartbeat_monitor_loop() -> None:
    while True:
        await asyncio.sleep(_CHECK_INTERVAL)
        try:
            await check_heartbeat_timeouts()
        except Exception:
            logger.exception("heartbeat_monitor error")
