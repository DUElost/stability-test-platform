from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from backend.core.metrics import record_plan_run_terminal
from backend.models.enums import JobStatus, PlanRunStatus
from backend.services.state_machine import PlanRunStateMachine

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
    PlanRunStatus.DEGRADED.value,
}


def apply_plan_run_aggregation(run: Any, jobs: Sequence[Any]) -> bool:
    """Apply the shared PlanRun terminal aggregation rule."""
    # Why: aggregator(async/sync) + abort 三处都会落终态,无守卫时第二个写入会覆盖第一个
    #      (例如 abort 后 aggregator 又把 ABORTED 改回 SUCCESS),配合上游 SELECT ... FOR UPDATE
    #      保证 read-modify-write 串行化。
    if run.status in _TERMINAL_PLAN_RUN_STATUSES:
        return False
    if not all(JobStatus(j.status) in _TERMINAL for j in jobs):
        return False

    total = len(jobs)
    if total == 0:
        PlanRunStateMachine.transition(run, PlanRunStatus.FAILED, reason="empty_job_set")
        run.ended_at = datetime.now(timezone.utc)
        record_plan_run_terminal(run.status, pass_rate=0.0)
        return True

    failed_only = sum(1 for j in jobs if JobStatus(j.status) == JobStatus.FAILED)
    aborted = sum(1 for j in jobs if JobStatus(j.status) == JobStatus.ABORTED)

    # Why: 用户已发出 abort,但若所有 job 自然终态且 mix 仅含 COMPLETED/少量 FAILED,
    #      natural mix 会算出 SUCCESS/PARTIAL_SUCCESS,abort 意图会被静默吞掉。
    run_context = getattr(run, "run_context", None)
    abort_requested = (
        isinstance(run_context, dict) and "abort_requested" in run_context
    )

    # v3: abort 是有意终止信号, 不参与 failure_threshold 容忍判断;
    #     任何 ABORTED → FAILED (覆盖 threshold).
    # 先在本地变量算出目标态、应用 abort override,最后统一走一次状态机
    # transition:SUCCESS/PARTIAL_SUCCESS 是终态,若边算边写会导致 override
    # 分支尝试做 SUCCESS→FAILED 这类非法的终态间跳转。
    if failed_only + aborted == 0:
        new_status = PlanRunStatus.SUCCESS
    elif aborted > 0:
        new_status = PlanRunStatus.FAILED
    elif failed_only / total <= run.failure_threshold:
        new_status = PlanRunStatus.PARTIAL_SUCCESS
    else:
        new_status = PlanRunStatus.FAILED

    # abort_requested override: 自然 SUCCESS/PARTIAL_SUCCESS 时强制 FAILED。
    # 不影响 FAILED(无需 override)。
    if abort_requested and new_status in (
        PlanRunStatus.SUCCESS,
        PlanRunStatus.PARTIAL_SUCCESS,
    ):
        new_status = PlanRunStatus.FAILED

    PlanRunStateMachine.transition(run, new_status, reason="aggregation")
    run.ended_at = datetime.now(timezone.utc)

    completed = sum(1 for j in jobs if JobStatus(j.status) == JobStatus.COMPLETED)
    pass_rate = round(completed / total, 4) if total else 0
    run.result_summary = {
        "total": total,
        "completed": completed,
        "failed": failed_only + aborted,   # 兼容字段: terminal failure 总数
        "failed_only": failed_only,        # v3: 自然失败 (不含 aborted)
        "aborted": aborted,                # v3: abort 终止数
        "unknown": 0,
        "pass_rate": pass_rate,
        "abort_requested": abort_requested,  # v3: 区分自然 FAILED vs abort 触发的 FAILED
    }
    record_plan_run_terminal(run.status, pass_rate=float(pass_rate))
    return True
