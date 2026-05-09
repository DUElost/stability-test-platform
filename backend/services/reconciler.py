"""State reconciler: idempotent StepTrace upsert from Agent replay."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.enums import JobStatus
from backend.models.job import JobInstance, StepTrace
from backend.services.aggregator import PlanAggregator
from backend.services.state_machine import InvalidTransitionError, JobStateMachine

logger = logging.getLogger(__name__)

_TERMINAL = {
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.ABORTED.value,
    JobStatus.UNKNOWN.value,
}


async def reconcile_step_traces(
    host_id: str,
    traces: List[dict],
    db: AsyncSession,
) -> dict:
    """
    Idempotently insert StepTraces from Agent replay.

    Returns ``{"inserted": int, "transitioned_jobs": list[int]}``.
    Unique constraint (job_id, step_id, event_type) prevents duplicates.
    """
    inserted = 0
    affected_jobs: set[int] = set()

    for t in traces:
        stmt = (
            pg_insert(StepTrace)
            .values(
                job_id=t["job_id"],
                step_id=t["step_id"],
                stage=t.get("stage", "execute"),
                event_type=t["event_type"],
                status=t.get("status", ""),
                output=t.get("output"),
                error_message=t.get("error_message"),
                original_ts=_parse_ts(t.get("original_ts")),
                created_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_nothing(
                constraint="uq_step_trace_idempotent"
            )
        )
        result = await db.execute(stmt)
        if result.rowcount > 0:
            inserted += 1
            affected_jobs.add(int(t["job_id"]))

    transitioned_jobs: list[int] = []
    for job_id in affected_jobs:
        job_ok, _plan_run_terminal = await _recompute_job_status(job_id, db)
        if job_ok:
            transitioned_jobs.append(job_id)

    await db.commit()
    logger.info(
        "reconcile: host=%s inserted=%d/%d transitioned=%d",
        host_id, inserted, len(traces), len(transitioned_jobs),
    )
    return {"inserted": inserted, "transitioned_jobs": transitioned_jobs}


async def _recompute_job_status(
    job_id: int, db: AsyncSession,
) -> tuple[bool, bool]:
    """Transition UNKNOWN/RUNNING jobs to terminal based on StepTrace events.

    Returns ``(job_transitioned, plan_run_terminal)``.
    """
    job = await db.get(JobInstance, job_id)
    if job is None or job.status in _TERMINAL:
        return False, False

    traces_result = await db.execute(
        select(StepTrace).where(StepTrace.job_id == job_id)
    )
    traces = traces_result.scalars().all()

    has_failed = any(t.event_type == "FAILED" for t in traces)
    has_completed = any(t.event_type == "COMPLETED" for t in traces)

    target: JobStatus | None = None
    if has_failed:
        target = JobStatus.FAILED
    elif has_completed and job.status == JobStatus.UNKNOWN.value:
        target = JobStatus.COMPLETED
    else:
        return False, False

    plan_run_terminal = False
    try:
        if target == JobStatus.COMPLETED and job.status == JobStatus.UNKNOWN.value:
            JobStateMachine.transition(job, JobStatus.RUNNING, "reconciled")
        JobStateMachine.transition(job, target, "reconciled_from_replay")
        job.ended_at = datetime.now(timezone.utc)
        # on_job_terminal may transition PlanRun; caller pushes after commit
        applied, _ = await PlanAggregator.on_job_terminal(job, db)
        plan_run_terminal = applied
    except InvalidTransitionError as e:
        logger.warning("reconcile transition blocked: %s", e)

    return True, plan_run_terminal


def _parse_ts(ts_str: str | None) -> datetime:
    if not ts_str:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
