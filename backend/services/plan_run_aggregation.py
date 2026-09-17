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

_NOTIFY_AS_FAILED = {PlanRunStatus.FAILED}

#: #1591-④ 裁决（2026-09-17）：越过这些脚本的 job，其**刷机目标**已经达成——这类 job
#: 之后再失败的批次不该被整体抹成 FAILED（实证：31 台批次里 14 台的 flash 步 COMPLETED
#: 却在 oobe/root 失败，run 一律 FAILED 掩盖了刷机成果）。
#: 只对**里程碑之后**的失败放宽：任一 FAILED job 的里程碑步没 COMPLETED、或存在 abort，
#: 都仍判 FAILED（保守）。脚本名耦合在本仓有先例（`LEGACY_AEE_SCRIPT_NAMES` /
#: `_WIFI_CONSUMER_SCRIPT_NAMES`）；新增里程碑脚本需同样经过裁决。
_MILESTONE_SCRIPT_NAMES = ("flash_firmware",)
_MILESTONE_STEP_DONE = "COMPLETED"


def _milestone_step_keys(run: Any) -> set[str]:
    """run 的 plan 快照里属于里程碑脚本的 step_key（快照缺 steps → 空集）。"""
    snapshot = getattr(run, "plan_snapshot", None)
    steps = snapshot.get("steps") if isinstance(snapshot, dict) else None
    if not isinstance(steps, list):
        return set()
    keys = {
        str(step.get("step_key") or "")
        for step in steps
        if isinstance(step, dict)
        and str(step.get("script_name") or "") in _MILESTONE_SCRIPT_NAMES
    }
    return {key for key in keys if key}


def _failed_jobs_all_past_milestone(
    db: Any, run: Any, *, failed_only: int, aborted: int,
) -> bool:
    """run 内**每个** FAILED job 都已越过里程碑（里程碑步 COMPLETED）时返回 True。

    只在 ``failed_only > 0`` 且无 abort 时可能为真；任何一处读不到（无 db、无里程碑
    步骤、计数与实际行不一致、查库异常）都返回 False —— **保守即维持今天的 FAILED**，
    绝不因为判据读不到而放宽整批判定。
    """
    if db is None or failed_only <= 0 or aborted > 0:
        return False
    keys = _milestone_step_keys(run)
    if not keys:
        return False
    try:
        from sqlalchemy import select

        from backend.models.job import JobInstance, StepTrace

        failed_ids = [
            int(job_id)
            for (job_id,) in db.execute(
                select(JobInstance.id).where(
                    JobInstance.plan_run_id == run.id,
                    JobInstance.status == JobStatus.FAILED.value,
                )
            ).all()
        ]
        if len(failed_ids) != failed_only:
            # 计数与实际行不一致（并发终态/漂移）→ 保守
            return False
        done = {
            int(job_id)
            for (job_id,) in db.execute(
                select(StepTrace.job_id)
                .where(
                    StepTrace.job_id.in_(failed_ids),
                    StepTrace.step_id.in_(keys),
                    StepTrace.status == _MILESTONE_STEP_DONE,
                )
                .distinct()
            ).all()
        }
        return len(done) == len(set(failed_ids))
    except Exception:
        logger.warning(
            "plan_run_milestone_probe_failed run=%s", getattr(run, "id", "?"),
            exc_info=True,
        )
        return False


def _resolve_plan_run_status(
    *,
    total: int,
    failed_only: int,
    aborted: int,
    failure_threshold: float,
    abort_requested: bool,
    post_milestone_failures: bool = False,
) -> PlanRunStatus:
    """Resolve the terminal PlanRun status.

    Ruled semantics (#783, 2026-09-12): ``aborted > 0`` intentionally forces
    ``FAILED`` regardless of the ``failed_only/total`` threshold. An abort is
    operator-attributable and means the run did not complete its intended
    coverage, so a partially-aborted run must not surface as SUCCESS/
    PARTIAL_SUCCESS. ``abort_requested`` additionally taints any otherwise
    successful/partial run. Changing this needs a product ruling (see Revisit).
    """
    if failed_only + aborted == 0:
        new_status = PlanRunStatus.SUCCESS
    elif aborted > 0:
        new_status = PlanRunStatus.FAILED
    elif post_milestone_failures:
        # #1591-④：失败全部发生在里程碑（刷机）之后 —— 刷机目标已达成，
        # 不该因后续步骤（oobe/root/aee_prepare）把整批判成 FAILED。
        new_status = PlanRunStatus.PARTIAL_SUCCESS
    elif failed_only / total <= failure_threshold:
        new_status = PlanRunStatus.PARTIAL_SUCCESS
    else:
        new_status = PlanRunStatus.FAILED

    if abort_requested and new_status in (
        PlanRunStatus.SUCCESS,
        PlanRunStatus.PARTIAL_SUCCESS,
    ):
        new_status = PlanRunStatus.FAILED
    return new_status


def _abort_requested(run: Any) -> bool:
    run_context = getattr(run, "run_context", None)
    return isinstance(run_context, dict) and "abort_requested" in run_context


def notify_plan_run_terminal(
    run: Any,
    *,
    new_status: PlanRunStatus | str,
    error_message: str,
) -> None:
    """Best-effort PlanRun-level notification (once per terminalization)."""
    try:
        from backend.services.notification_service import dispatch_notification_async

        status = (
            new_status
            if isinstance(new_status, PlanRunStatus)
            else PlanRunStatus(new_status)
        )
        event_type = (
            "RUN_FAILED" if status in _NOTIFY_AS_FAILED else "RUN_COMPLETED"
        )
        dispatch_notification_async(event_type, {
            "run_id": int(run.id),
            "plan_id": int(getattr(run, "plan_id", 0) or 0),
            "task_name": f"plan-run-{run.id}",
            "task_type": "plan",
            "device_serial": "",
            "error_message": error_message,
        })
    except Exception:
        logger.exception(
            "plan_run_terminal_notification_failed plan_run=%s status=%s",
            getattr(run, "id", None),
            getattr(new_status, "value", new_status),
        )


# Backward-compatible private alias used by this module.
_notify_plan_run_terminal = notify_plan_run_terminal


def maybe_notify_risk_high(
    db: Any,
    *,
    plan_run_id: int | None,
    risk_summary: dict[str, Any] | None,
) -> bool:
    """Emit RISK_HIGH once when AEE/ANR aggregation reaches level S.

    Deduped via ``run_context.risk_high_notified`` so multi-job post-completion
    does not spam. Returns True when a notification was dispatched.
    """
    if not plan_run_id or not isinstance(risk_summary, dict):
        return False
    if str(risk_summary.get("risk_level", "")).upper() != "S":
        return False

    try:
        from sqlalchemy import select
        from sqlalchemy.orm.attributes import flag_modified

        from backend.models.plan_run import PlanRun
        from backend.services.notification_service import dispatch_notification_async

        pr = db.execute(
            select(PlanRun)
            .where(PlanRun.id == int(plan_run_id))
            .with_for_update(key_share=True)  # SQLAlchemy key_share → PG FOR NO KEY UPDATE (#1473)
        ).scalar_one_or_none()
        if pr is None:
            return False

        run_ctx = dict(pr.run_context or {})
        if run_ctx.get("risk_high_notified"):
            return False

        run_ctx["risk_high_notified"] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "risk_level": "S",
            "counts": (risk_summary.get("counts") or {}),
        }
        pr.run_context = run_ctx
        if hasattr(pr, "_sa_instance_state"):
            flag_modified(pr, "run_context")
        db.commit()

        counts = risk_summary.get("counts") if isinstance(risk_summary.get("counts"), dict) else {}
        by_type = counts.get("by_type") if isinstance(counts.get("by_type"), dict) else {}
        type_bits = ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())[:12])
        dispatch_notification_async("RISK_HIGH", {
            "run_id": int(pr.id),
            "plan_id": int(getattr(pr, "plan_id", 0) or 0),
            "task_name": f"plan-run-{pr.id}",
            "task_type": "plan",
            "risk_summary": (
                f"PlanRun {pr.id} risk_level=S"
                + (f" ({type_bits})" if type_bits else "")
            ),
            "risk_level": "S",
            "counts": counts,
        })
        return True
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        logger.exception(
            "plan_run_risk_high_notification_failed plan_run=%s", plan_run_id,
        )
        return False


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
    PlanRunStateMachine.transition(run, new_status, reason="aggregation")
    run.ended_at = datetime.now(timezone.utc)
    pass_rate = round(completed / total, 4) if total else 0
    run.result_summary = {
        "total": total,
        "completed": completed,
        "failed": failed_only + aborted,
        "failed_only": failed_only,
        "aborted": aborted,
        "unknown": 0,
        "pass_rate": pass_rate,
        "abort_requested": abort_requested,
    }
    record_plan_run_terminal(run.status, pass_rate=float(pass_rate))
    failed = failed_only + aborted
    _notify_plan_run_terminal(
        run,
        new_status=new_status,
        error_message=(
            f"PlanRun {new_status.value}: {completed}/{total} completed, "
            f"{failed} failed"
        ),
    )
    # #1082：终态后数据静止 —— 批量重算各 job 的报告缓存，快照从此 = 最终结果。
    # Best-effort 后台执行（重算 N 份报告不阻塞聚合事务）；调度失败放弃本轮，
    # /report/cached 的 live 兜底仍给出正确数据。
    from backend.services.post_completion import _schedule_report_cache_refresh

    _schedule_report_cache_refresh(int(run.id))
    return True


def apply_plan_run_aggregation_from_counters(run: Any, *, db: Any = None) -> bool:
    """O(1) aggregation from plan_run counters (ADR-0026 §6).

    Requires ``total_job_count > 0`` and ``terminal_job_count >= total_job_count``.

    ``db`` 仅在有失败 job 时用于一次里程碑探测（#1591-④）——无失败仍是严格 O(1）；
    不传（测试/无会话调用方）时跳过里程碑判定，语义回到阈值规则。
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
        total=total,
        failed_only=failed_only,
        aborted=aborted,
        failure_threshold=float(run.failure_threshold),
        abort_requested=abort_requested,
        post_milestone_failures=_failed_jobs_all_past_milestone(
            db, run, failed_only=failed_only, aborted=aborted,
        ),
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

    ``db`` 用于失败时的里程碑探测（#1591-④）；与计数器路径同语义。
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
        PlanRunStateMachine.transition(run, PlanRunStatus.FAILED, reason="empty_job_set")
        run.ended_at = datetime.now(timezone.utc)
        record_plan_run_terminal(run.status, pass_rate=0.0)
        _notify_plan_run_terminal(
            run,
            new_status=PlanRunStatus.FAILED,
            error_message="PlanRun FAILED: no jobs were created for this plan",
        )
        return True

    failed_only = sum(1 for j in jobs if JobStatus(j.status) == JobStatus.FAILED)
    aborted = sum(1 for j in jobs if JobStatus(j.status) == JobStatus.ABORTED)
    completed = sum(1 for j in jobs if JobStatus(j.status) == JobStatus.COMPLETED)
    abort_requested = _abort_requested(run)

    new_status = _resolve_plan_run_status(
        total=total,
        failed_only=failed_only,
        aborted=aborted,
        failure_threshold=float(run.failure_threshold),
        abort_requested=abort_requested,
        post_milestone_failures=_failed_jobs_all_past_milestone(
            db, run, failed_only=failed_only, aborted=aborted,
        ),
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
