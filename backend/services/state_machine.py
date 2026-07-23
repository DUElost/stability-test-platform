from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from backend.models.enums import JobStatus, PlanRunStatus
from backend.models.job import JobInstance

logger = logging.getLogger(__name__)

VALID_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.PENDING:      {JobStatus.RUNNING, JobStatus.FAILED, JobStatus.ABORTED},
    JobStatus.RUNNING:      {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.ABORTED, JobStatus.UNKNOWN},
    # UNKNOWN is a fenced recovery state.  A late Agent completion must first
    # re-establish the lease through recovery (UNKNOWN → RUNNING); otherwise
    # grace expiry is the only legal terminal path (UNKNOWN → FAILED).
    JobStatus.UNKNOWN:      {JobStatus.RUNNING, JobStatus.FAILED},
    JobStatus.FAILED:       set(),
    JobStatus.COMPLETED:    set(),
    JobStatus.ABORTED:      set(),
}


class InvalidTransitionError(Exception):
    pass


class JobStateMachine:
    @staticmethod
    def transition(job: JobInstance, new_status: JobStatus, reason: str = "") -> None:
        try:
            current = JobStatus(job.status)
        except ValueError:
            raise InvalidTransitionError(f"Unknown job status '{job.status}' for job {job.id}")
        if new_status not in VALID_TRANSITIONS[current]:
            raise InvalidTransitionError(
                f"Cannot transition {job.status} -> {new_status} for job {job.id}"
            )
        job.status = new_status.value
        job.status_reason = reason
        job.updated_at = datetime.now(timezone.utc)


# PlanRun 无 status_reason/updated_at 列(与 JobInstance 不同),transition() 的
# reason 仅用于 debug 日志留痕,不落库。
PLAN_RUN_VALID_TRANSITIONS: dict[PlanRunStatus, set[PlanRunStatus]] = {
    # ── ADR-0026 admission queue(feature flag 控制,P1 step 2 起 schema 就绪)──
    # QUEUED 重新排队(竞争回队/退避)只更新排队字段,不走状态机自环。
    PlanRunStatus.QUEUED: {
        PlanRunStatus.PRECHECK,   # queue pump 取得所有权,进入准入预检
        PlanRunStatus.FAILED,     # abort / 不可重试错误(配置失效等)
    },
    PlanRunStatus.PRECHECK: {
        PlanRunStatus.QUEUED,     # 准入竞争失败回队 / reaper stale recovery(不变量④)
        PlanRunStatus.RUNNING,    # admission transaction 提交,全部 Job 已物化
        PlanRunStatus.FAILED,     # abort / 不可重试错误 / 重试次数耗尽
    },
    PlanRunStatus.RUNNING: {
        PlanRunStatus.SUCCESS,
        PlanRunStatus.PARTIAL_SUCCESS,
        PlanRunStatus.FAILED,
    },
    # V2-only retry (admission queue).
    PlanRunStatus.FAILED: {PlanRunStatus.QUEUED},
    PlanRunStatus.SUCCESS:         set(),
    PlanRunStatus.PARTIAL_SUCCESS: set(),
    PlanRunStatus.DEGRADED:        set(),
}


class PlanRunStateMachine:
    @staticmethod
    def transition(run: Any, new_status: PlanRunStatus, reason: str = "") -> None:
        try:
            current = PlanRunStatus(run.status)
        except ValueError:
            raise InvalidTransitionError(
                f"Unknown plan_run status '{run.status}' for plan_run {getattr(run, 'id', '?')}"
            )
        if new_status not in PLAN_RUN_VALID_TRANSITIONS[current]:
            raise InvalidTransitionError(
                f"Cannot transition {run.status} -> {new_status} for plan_run {getattr(run, 'id', '?')}"
            )
        run.status = new_status.value
        logger.debug(
            "plan_run_transition plan_run=%s %s -> %s reason=%s",
            getattr(run, "id", "?"), current.value, new_status.value, reason,
        )
