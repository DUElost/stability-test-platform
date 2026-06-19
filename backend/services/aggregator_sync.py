"""Synchronous plan-run aggregation for use in sync contexts (recycler thread)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun
from backend.services.plan_run_aggregation import apply_plan_run_aggregation
from backend.services.plan_chain_trigger import trigger_next_plan_sync


def plan_aggregator_sync(job: JobInstance, db: Session) -> None:
    """Aggregate a PlanRun after a child JobInstance reaches terminal state."""
    # Why: 与 async aggregator 走同一份锁契约,recycler 多线程或与 abort 并发都不能丢更新。
    run = db.execute(
        select(PlanRun)
        .where(PlanRun.id == job.plan_run_id)
        .with_for_update()
    ).scalar_one_or_none()
    if run is None:
        return

    jobs = (
        db.query(JobInstance)
        .filter(JobInstance.plan_run_id == run.id)
        .all()
    )

    applied = apply_plan_run_aggregation(run, jobs)
    if applied:
        trigger_next_plan_sync(run, db)
        # ADR-0025 Sprint 4: PlanRun 终态统一触发归档-2 scan + merge
        from backend.services.dedup_scan import should_trigger_dedup, enqueue_dedup_terminal_sync
        if should_trigger_dedup(run.status):
            enqueue_dedup_terminal_sync(run.id)
