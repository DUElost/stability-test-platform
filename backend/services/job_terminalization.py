"""ADR-0052 D1/D2 — Job 终态事务与父 Run 聚合解耦：只写事实 + durable pending 标记。

**新形状（#3244 / ADR-0052）**：``on_job_terminal``(_sync) 在调用方的终态事务里
向 ``plan_run_pending_aggregation`` **插一行**（insert-only，``(plan_run_id,
job_id)`` 复合主键 + ON CONFLICT DO NOTHING ⇒ outbox 重放幂等），随后**自行
commit**（保持 #986/#1172/#2531/#2635 的边界契约：调用方不得在 begin_nested 内
调用，返回后不得假定原事务仍开、且一候选一提交点成立），提交后唤醒父 Run 聚合者：

- 生产：SAQ ``function="aggregate_plan_run_task"``、``key="agg:{plan_run_id}"``
  （按 key 去重 ⇒ 短窗内数百终态合并成一次聚合唤醒；Redis 仅传输，事实源是
  pending 表）。入队失败只告警——``counter_reconciler`` 的 300s sweep 扫描
  pending 表重放（ADR-0052 D2/D4 恢复矩阵）。
- ``TESTING=1``：无进程内 worker 等待语义，直接**同步排空**聚合
  （等价旧「终态即聚合」行为，测试断言面不漂移）。

**该事务不再**（D1）：锁 ``plan_run`` 行、自增 ``plan_run`` / ``plan_run_host``
计数器、读改写 ``acknowledged_job_ids``（D5：ACK 语义改由 Job 终态推导——
``run_context.abort_requested`` 的 Job 级追写全删，abort 入口的初始空数组与
保留合并仍由 ``plan_run_abort`` 维护）。

继承 ADR-0026 §6 的不变量：单一 terminalization 入口（所有终态入口仍经本服务）、
五列计数语义（判定输入见 ADR-0048 D1）、集中服务 + 低频对账 sweep 自愈。
计数的产生方式由「终态事务内自增」改为「聚合者读 Job 事实重算」（ADR-0052 §3）。

批量调用方的正确形状（#2531）不变：**逐候选**「savepoint 落库 → 退出 savepoint →
立刻为该候选调用本服务」；边界现由本模块的 commit 独立成立（不再依赖
``applied=True``——旧「末位候选才提交」形态正是 #2787 记录的缺陷根源）。

返回值语义变化：旧版返回 ``(applied, run_status)``（末位 Job 同步判定父终态）；
父终态改由聚合器异步判定后，本函数返回 ``(pending_written, None)``，第二元素
仅为兼容旧调用形状保留。
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.models.enums import JobStatus
from backend.models.job import JobInstance

logger = logging.getLogger(__name__)

_TERMINAL = {
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.ABORTED.value,
}


def _is_testing_env() -> bool:
    return os.getenv("TESTING") == "1"


def _pending_insert_stmt(plan_run_id: int, job_id: int):
    """insert-only 标记；主键冲突（outbox 重放同终态）时 no-op。"""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from backend.models.plan_run import PlanRunPendingAggregation

    return pg_insert(PlanRunPendingAggregation).values(
        plan_run_id=int(plan_run_id),
        job_id=int(job_id),
    ).on_conflict_do_nothing(index_elements=["plan_run_id", "job_id"])


async def _wake_parent_aggregation_async(plan_run_id: int) -> None:
    """提交后唤醒聚合者。永不外溢异常（唤醒丢失 = 延迟，事实不丢，D4 恢复矩阵）。"""
    if _is_testing_env():
        import asyncio

        from backend.services.plan_run_finalization import (
            drain_plan_run_aggregation_sync,
        )

        try:
            # 与生产 SAQ 任务同一执行器（sync 核心 + 线程），测试语义 = 旧「终态
            # 即聚合」：本函数返回时父 Run 计数/终态已收敛。
            await asyncio.to_thread(drain_plan_run_aggregation_sync, plan_run_id)
        except Exception:
            logger.exception(
                "aggregate_inline_drain_failed_async plan_run=%s", plan_run_id,
            )
        return

    try:
        from saq import Job as SaqJob

        from backend.core.task_queue import get_queue

        await get_queue().enqueue(
            SaqJob(
                function="aggregate_plan_run_task",
                kwargs={"plan_run_id": int(plan_run_id)},
                key=f"agg:{plan_run_id}",
                timeout=120,
                retries=3,
                retry_delay=2.0,
            )
        )
    except Exception as exc:
        logger.warning(
            "aggregate_wakeup_failed plan_run=%s err=%s "
            "— pending 表仍在，由 counter_reconciler 重放",
            plan_run_id, exc,
        )


def _wake_parent_aggregation_sync(plan_run_id: int) -> None:
    """Sync counterpart（recycler / reaper 线程语境）。"""
    if _is_testing_env():
        from backend.services.plan_run_finalization import (
            drain_plan_run_aggregation_sync,
        )

        try:
            drain_plan_run_aggregation_sync(plan_run_id)
        except Exception:
            logger.exception(
                "aggregate_inline_drain_failed_sync plan_run=%s", plan_run_id,
            )
        return

    try:
        from backend.core.task_queue import enqueue_sync

        enqueue_sync(
            "aggregate_plan_run_task",
            key=f"agg:{plan_run_id}",
            timeout=120,
            retries=3,
            plan_run_id=int(plan_run_id),
        )
    except Exception as exc:
        logger.warning(
            "aggregate_wakeup_failed plan_run=%s err=%s "
            "— pending 表仍在，由 counter_reconciler 重放",
            plan_run_id, exc,
        )


async def on_job_terminal(
    job: JobInstance, db: AsyncSession,
) -> tuple[bool, Optional[str]]:
    """Async entry — Agent ``/complete``, session_watchdog, lease reconciler."""
    if job.status not in _TERMINAL:
        logger.warning(
            "on_job_terminal_skipped_non_terminal job=%s status=%s",
            job.id, job.status,
        )
        return False, None

    await db.execute(_pending_insert_stmt(job.plan_run_id, job.id))
    # 终态事实 + pending 标记同一事务一次提交（#1172 边界契约：自管理提交）。
    await db.commit()
    await _wake_parent_aggregation_async(int(job.plan_run_id))
    return True, None


def on_job_terminal_sync(
    job: JobInstance,
    db: Session,
) -> tuple[bool, Optional[str]]:
    """Sync entry — recycler / reaper 线程语境（同 async 版语义）。

    旧版可选 ``run`` 参数（预锁父行）随 D1 移除父行锁而废止。
    """
    if job.status not in _TERMINAL:
        logger.warning(
            "on_job_terminal_sync_skipped_non_terminal job=%s status=%s",
            job.id, job.status,
        )
        return False, None

    db.execute(_pending_insert_stmt(job.plan_run_id, job.id))
    db.commit()
    _wake_parent_aggregation_sync(int(job.plan_run_id))
    return True, None


# 兼容再导出：旧导入路径（reconciler/tests 曾从本模块取计数重算）。实现随
# ADR-0052 #3244 迁入 plan_run_aggregation（依赖方向：编排者/终态 → 聚合器，
# 终态事务不再消费聚合判定）。
from backend.services.plan_run_aggregation import (  # noqa: E402,F401
    recount_plan_run_counters,
)
