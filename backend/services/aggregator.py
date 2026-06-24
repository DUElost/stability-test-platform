"""Async PlanRun aggregator entrypoint (ADR-0020).

Triggered by Agent ``/complete`` (via ``backend.api.routes.agent_api``)
and the SAQ post-completion task.  Locates the parent ``PlanRun``, applies
the shared aggregation rule, then triggers the next chain segment if the
PlanRun reached a triggerable terminal status.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun
from backend.services.plan_run_aggregation import apply_plan_run_aggregation
from backend.services.plan_chain_trigger import trigger_next_plan


class PlanAggregator:
    """ADR-0020: PlanRun terminal aggregation + chain trigger."""

    @staticmethod
    async def on_job_terminal(
        job: JobInstance, db: AsyncSession,
    ) -> tuple[bool, str | None]:
        """Returns ``(applied, new_status)`` — caller should push after commit."""
        # Why: 多个 Job 同帧终态会触发并发聚合;不持锁则两个写者各自基于自己的视图覆盖
        #      最终状态。SELECT ... FOR NO KEY UPDATE 串行 read-modify-write,配合 aggregation
        #      侧的 _TERMINAL_PLAN_RUN_STATUSES 守卫保证幂等。
        # Why (lock mode): 用 FOR NO KEY UPDATE 而非 FOR UPDATE —— complete_job 在本事务
        #      里已 autoflush 了 UPDATE job_instance,FK plan_run_id→plan_run.id 会在
        #      plan_run 行上自动加 FOR KEY SHARE。FOR UPDATE 与 FOR KEY SHARE 冲突,
        #      两个并发 complete 同一 plan_run 的不同 job 会各持 KEY SHARE 互锁对方
        #      的 FOR UPDATE → 死锁。FOR NO KEY UPDATE 与 FOR KEY SHARE 兼容,消除循环
        #      等待;两个 FOR NO KEY UPDATE 仍互相冲突,串行化保证不变。aggregation 只
        #      改 status/ended_at/result_summary(非 key 列),NO KEY UPDATE 保护等价。
        run = (
            await db.execute(
                select(PlanRun)
                .where(PlanRun.id == job.plan_run_id)
                .with_for_update(key_share=True)
            )
        ).scalar_one_or_none()
        if run is None:
            return False, None

        result = await db.execute(
            select(JobInstance).where(JobInstance.plan_run_id == run.id)
        )
        jobs = result.scalars().all()

        applied = apply_plan_run_aggregation(run, jobs)
        if applied:
            await trigger_next_plan(run, db)
            # ADR-0025 Sprint 4: PlanRun 终态统一触发归档-2 scan + merge
            from backend.services.dedup_scan import should_trigger_dedup, enqueue_dedup_terminal_async
            if should_trigger_dedup(run.status):
                await enqueue_dedup_terminal_async(run.id)
        return applied, run.status if applied else None
