"""PlanRun 终态聚合：纯计算 + 落库（#3299 收口）。

本模块只回答「所有 job 落终态后，父 Run 该是什么状态、事实写进哪些列」；
**终态之后做什么**（RUN_* 通知、报告缓存刷新、链式触发、dedup 入队、RISK_HIGH、
链恢复）一律归 ``backend.services.plan_run_finalization`` 编排。``apply_*`` 的
调用方在返回 True 后经编排者反应（``finalize_parent_run_*`` /
``announce_parent_terminal``）。不得再在本模块 import 任何上游副作用模块——
那正是本单解开的五模块环的闭合边。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from backend.core.metrics import record_plan_run_terminal
from backend.models.enums import JobStatus, PlanRunStatus
from backend.services.state_machine import PlanRunStateMachine

logger = logging.getLogger(__name__)

_TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.ABORTED}
# UNKNOWN is intentionally excluded: with the B4 fix (agent_api.py _TERMINAL no
# longer contains UNKNOWN), an UNKNOWN job may still transition to COMPLETED/
# FAILED via Agent completion.  PlanRun aggregation must wait until every job
# reaches a truly final state; the reconciler will eventually convert UNKNOWN→
# FAILED when the grace window expires.

_TERMINAL_PLAN_RUN_STATUSES = {
    PlanRunStatus.SUCCESS.value,
    PlanRunStatus.PARTIAL_SUCCESS.value,
    PlanRunStatus.FAILED.value,
}

def _resolve_plan_run_status(
    *,
    failed_only: int,
    aborted: int,
    abort_requested: bool,
) -> PlanRunStatus:
    """Resolve the terminal PlanRun status (ADR-0048 v1.1 semantics).

    稳定性平台的设备失败/掉线是施压测试的正常现象——run 状态只表达**执行链**：
    全部 job 落终态后，存在 abort（或 abort_requested）→ FAILED（#783：人工中止=
    未覆盖计划）；``failed_only > 0`` → PARTIAL_SUCCESS（黄：链完整跑完、过程有
    设备失败——**台数多少都不判红、不断链、不触发 RUN_FAILED**）；其余 → SUCCESS。
    阈值判定轴（``failure_threshold`` / #1591-④ 里程碑豁免）仍随 ADR-0048 v1.0
    废止：判定输入只有 ``failed_only/aborted/abort_requested`` 三个计数，
    重新引入比例线或豁免参数即违反 v1.1 裁决（结构测试钉住签名）。
    """
    if aborted > 0 or abort_requested:
        return PlanRunStatus.FAILED
    if failed_only > 0:
        return PlanRunStatus.PARTIAL_SUCCESS
    return PlanRunStatus.SUCCESS


def _abort_requested(run: Any) -> bool:
    run_context = getattr(run, "run_context", None)
    return isinstance(run_context, dict) and "abort_requested" in run_context


def _finalize_plan_run(
    run: Any,
    *,
    new_status: PlanRunStatus,
    total: int,
    completed: int,
    failed_only: int,
    aborted: int,
    abort_requested: bool,
) -> bool:
    """把终态事实写到 run 行上（纯计算 + 列赋值，#3299）。

    通知与报告缓存刷新**不再**发生在这里：调用方在 ``apply_*`` 返回 True 后经
    ``plan_run_finalization.announce_parent_terminal`` 反应，文案输入
    （``result_summary`` 的 total/completed/failed）就是本函数刚写入的值。
    """
    PlanRunStateMachine.transition(run, new_status, reason="aggregation")
    run.ended_at = datetime.now(timezone.utc)
    run.result_summary = {
        "total": total,
        "completed": completed,
        "failed": failed_only + aborted,
        "failed_only": failed_only,
        "aborted": aborted,
        "unknown": 0,
        "abort_requested": abort_requested,
    }
    record_plan_run_terminal(run.status)
    return True


def apply_plan_run_aggregation_from_counters(run: Any, *, db: Any = None) -> bool:
    """O(1) aggregation from plan_run counters (ADR-0026 §6).

    Requires ``total_job_count > 0`` and ``terminal_job_count >= total_job_count``.

    ``db`` 保留仅为调用方签名兼容（ADR-0048 后聚合判定不再消费会话）。
    """
    if run.status in _TERMINAL_PLAN_RUN_STATUSES:
        return False
    total = int(getattr(run, "total_job_count", 0) or 0)
    terminal = int(getattr(run, "terminal_job_count", 0) or 0)
    if total <= 0 or terminal < total:
        return False

    failed_only = int(getattr(run, "failed_job_count", 0) or 0)
    aborted = int(getattr(run, "aborted_job_count", 0) or 0)
    completed = int(getattr(run, "completed_job_count", 0) or 0)
    abort_requested = _abort_requested(run)
    new_status = _resolve_plan_run_status(
        failed_only=failed_only, aborted=aborted, abort_requested=abort_requested,
    )
    return _finalize_plan_run(
        run,
        new_status=new_status,
        total=total,
        completed=completed,
        failed_only=failed_only,
        aborted=aborted,
        abort_requested=abort_requested,
    )


def apply_plan_run_aggregation(run: Any, jobs: Sequence[Any], *, db: Any = None) -> bool:
    """Apply the shared PlanRun terminal aggregation rule (full job scan).

    ``db`` 保留仅为调用方签名兼容（ADR-0048 后判定不消费）。
    """
    # Why: aggregator(async/sync) + abort 三处都会落终态,无守卫时第二个写入会覆盖第一个
    #      (例如 abort 后 aggregator 又把 ABORTED 改回 SUCCESS),配合上游 SELECT ... FOR UPDATE
    #      保证 read-modify-write 串行化。
    if run.status in _TERMINAL_PLAN_RUN_STATUSES:
        return False
    if not all(JobStatus(j.status) in _TERMINAL for j in jobs):
        return False

    total = len(jobs)
    if total == 0:
        # 空集路径同样只落事实（#3299）；RUN_FAILED「no jobs」通知由调用方经
        # announce_parent_terminal(run, no_jobs=True) 发出。
        PlanRunStateMachine.transition(run, PlanRunStatus.FAILED, reason="empty_job_set")
        run.ended_at = datetime.now(timezone.utc)
        record_plan_run_terminal(run.status)
        return True

    failed_only = sum(1 for j in jobs if JobStatus(j.status) == JobStatus.FAILED)
    aborted = sum(1 for j in jobs if JobStatus(j.status) == JobStatus.ABORTED)
    completed = sum(1 for j in jobs if JobStatus(j.status) == JobStatus.COMPLETED)
    abort_requested = _abort_requested(run)

    new_status = _resolve_plan_run_status(
        failed_only=failed_only, aborted=aborted, abort_requested=abort_requested,
    )
    return _finalize_plan_run(
        run,
        new_status=new_status,
        total=total,
        completed=completed,
        failed_only=failed_only,
        aborted=aborted,
        abort_requested=abort_requested,
    )
