# -*- coding: utf-8 -*-
"""PlanRun 运维写操作（ADR-0031 附录 PR-C）——与 plan_runs API 同源。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.models.job import JobInstance, JobStatus
from backend.models.plan_run import PlanRun
from backend.services.ai_assistant.tools import ToolValidationError
from backend.services.plan_run_abort import PlanRunAbortError, abort_plan_run
from backend.services.precheck.runner import (
    PlanRunDispatchRetryError,
    retry_plan_run_dispatch,
)

logger = logging.getLogger(__name__)


def _parse_run_id(raw: dict | None) -> int:
    args = raw or {}
    try:
        run_id = int(args.get("run_id") or 0)
    except (TypeError, ValueError):
        raise ToolValidationError("run_id must be a positive integer") from None
    if run_id < 1:
        raise ToolValidationError("run_id must be a positive integer")
    return run_id


def _parse_run_and_job_ids(raw: dict | None) -> tuple[int, int]:
    args = raw or {}
    run_id = _parse_run_id(args)
    try:
        job_id = int(args.get("job_id") or 0)
    except (TypeError, ValueError):
        raise ToolValidationError("job_id must be a positive integer") from None
    if job_id < 1:
        raise ToolValidationError("job_id must be a positive integer")
    return run_id, job_id


def _optional_reason(raw: dict | None, *, default: str) -> str:
    reason = (raw or {}).get("reason")
    if reason is None:
        return default
    text = str(reason).strip()[:500]
    return text or default


def normalize_abort_params(args: dict | None) -> dict[str, Any]:
    return {"run_id": _parse_run_id(args), "reason": _optional_reason(args, default="aborted_by_user")}


def normalize_retry_dispatch_params(args: dict | None) -> dict[str, Any]:
    return {"run_id": _parse_run_id(args)}


def normalize_manual_job_params(args: dict | None, *, default_reason: str) -> dict[str, Any]:
    run_id, job_id = _parse_run_and_job_ids(args)
    return {
        "run_id": run_id,
        "job_id": job_id,
        "reason": _optional_reason(args, default=default_reason),
    }


def normalize_archive_params(args: dict | None) -> dict[str, Any]:
    return {"run_id": _parse_run_id(args)}


def describe_abort_preview(db: Session, params: dict) -> str:
    pr = db.get(PlanRun, params["run_id"])
    if pr is None:
        return f"PlanRun #{params['run_id']}（未找到）"
    plan_name = pr.plan.name if getattr(pr, "plan", None) else f"plan_id={pr.plan_id}"
    running = (
        db.query(JobInstance.id)
        .filter(
            JobInstance.plan_run_id == pr.id,
            JobInstance.status == JobStatus.RUNNING.value,
        )
        .count()
    )
    pending = (
        db.query(JobInstance.id)
        .filter(
            JobInstance.plan_run_id == pr.id,
            JobInstance.status == JobStatus.PENDING.value,
        )
        .count()
    )
    lines = [
        f"PlanRun #{pr.id} status={pr.status}",
        f"Plan：{plan_name!r}",
        f"RUNNING jobs：{running}，PENDING jobs：{pending}",
        f"中止原因：{params['reason']}",
    ]
    return "\n".join(lines)


def describe_manual_job_preview(db: Session, params: dict, *, action: str) -> str:
    from backend.services.plan_run_queries import load_job_in_run

    try:
        job = load_job_in_run(db, params["run_id"], params["job_id"])
    except HTTPException:
        return (
            f"PlanRun #{params['run_id']} job #{params['job_id']}（未找到或不属于该 run）"
        )
    device = job.device.serial if job.device else None
    return "\n".join([
        f"操作：{action}",
        f"PlanRun #{params['run_id']} job #{params['job_id']} status={job.status}",
        f"设备：{device or '—'} manual_action={job.manual_action or '—'}",
        f"原因：{params['reason']}",
    ])


def describe_retry_dispatch_preview(db: Session, params: dict) -> str:
    pr = db.get(PlanRun, params["run_id"])
    if pr is None:
        return f"PlanRun #{params['run_id']}（未找到）"
    run_ctx = dict(pr.run_context or {})
    return "\n".join([
        f"PlanRun #{pr.id} status={pr.status}",
        f"dispatch_device_ids：{len(run_ctx.get('dispatch_device_ids') or [])} 台",
        "将重入准入队列（须无 Job 且 precheck/dispatch 失败态）",
    ])


def describe_archive_preview(db: Session, params: dict) -> str:
    from backend.services.plan_run_scan_scope import iter_plan_run_scan_hosts

    pr = db.get(PlanRun, params["run_id"])
    if pr is None:
        return f"PlanRun #{params['run_id']}（未找到）"
    host_rows = iter_plan_run_scan_hosts(db, params["run_id"])
    online = [h for h, st, retired in host_rows if st == "ONLINE" and not retired]
    offline = [h for h, st, retired in host_rows if st != "ONLINE" and not retired]
    retired = [h for h, _st, is_retired in host_rows if is_retired]
    lines = [
        f"PlanRun #{pr.id} status={pr.status}",
        f"将 archive_now + scan_now 下发至 ONLINE host（{len(online)}）：{', '.join(online) or '—'}",
    ]
    if offline:
        lines.append(f"跳过 OFFLINE host（{len(offline)}）：{', '.join(offline)}")
    if retired:
        lines.append(
            f"跳过退役 host（{len(retired)}，ADR-0038 D5：仅显式 admin 触发可触达）："
            f"{', '.join(retired)}"
        )
    return "\n".join(lines)


def _http_exception_to_runtime(exc: HTTPException) -> RuntimeError:
    detail = exc.detail
    if isinstance(detail, dict):
        detail = detail.get("message") or str(detail)
    return RuntimeError(str(detail))


def run_abort_plan_run(
    db: Session,
    params: dict,
    *,
    triggered_by: str,
    requester_user_id: int | None = None,
) -> str:
    try:
        summary = abort_plan_run(
            params["run_id"],
            db=db,
            reason=params["reason"],
            triggered_by=triggered_by,
            audit_user_id=requester_user_id,
            audit_username=triggered_by,
            audit_action="ai_assistant_abort_plan_run",
        )
    except PlanRunAbortError as exc:
        raise RuntimeError(str(exc)) from exc
    return (
        f"PlanRun #{summary['plan_run_id']} 已中止（status={summary['status']}，"
        f"phase={summary.get('phase', '—')}，aborted_jobs={len(summary.get('aborted_jobs') or [])}）"
    )


def run_retry_plan_run_dispatch(
    db: Session,
    params: dict,
    *,
    triggered_by: str,
    requester_user_id: int | None = None,
) -> str:
    try:
        summary = retry_plan_run_dispatch(
            params["run_id"],
            db=db,
            triggered_by=triggered_by,
            audit_user_id=requester_user_id,
        )
    except PlanRunDispatchRetryError as exc:
        raise RuntimeError(str(exc)) from exc
    return (
        f"PlanRun #{summary['plan_run_id']} 已重入队（status={summary['status']}）"
    )


def run_manual_retry_job(
    db: Session,
    params: dict,
    *,
    triggered_by: str,
    requester_user_id: int | None = None,
) -> str:
    from backend.services.plan_run_events import emit_job_status_invalidation
    from backend.services.plan_run_queries import (
        MANUAL_ACTION_JOB_STATUSES,
        device_currently_disconnected,
        load_job_in_run,
    )
    from backend.core.audit import record_audit
    from backend.core.metrics import record_patrol_manual_action
    from backend.models.host import Device, Host

    run_id = params["run_id"]
    job_id = params["job_id"]
    try:
        job = load_job_in_run(db, run_id, job_id)
    except HTTPException as exc:
        raise _http_exception_to_runtime(exc) from exc

    if job.status not in MANUAL_ACTION_JOB_STATUSES:
        raise RuntimeError(
            f"job must be RUNNING for manual retry; current status is {job.status}"
        )

    device = db.get(Device, job.device_id) if job.device_id else None
    host_status: str | None = None
    if job.host_id:
        host_row = db.get(Host, job.host_id)
        host_status = host_row.status if host_row else None
    if device_currently_disconnected(device, host_status):
        raise RuntimeError(
            "device ADB is not reachable; manual retry cannot restore connection"
        )

    if job.manual_action == "RETRY_NOW":
        return (
            f"job #{job_id} 已处于 RETRY_NOW（status={job.status}），无需重复操作"
        )

    reason = params["reason"]
    now = datetime.now(timezone.utc)
    job.next_retry_at = now
    job.manual_action = "RETRY_NOW"
    job.updated_at = now
    db.flush()

    record_audit(
        db,
        action="patrol_manual_retry",
        resource_type="job_instance",
        resource_id=job_id,
        details={
            "plan_run_id": run_id,
            "reason": reason,
            "current_failure_streak": job.current_failure_streak or 0,
            "triggered_by": triggered_by,
            "via": "ai_assistant",
        },
        user_id=requester_user_id,
        username=triggered_by,
    )
    db.commit()
    db.refresh(job)
    record_patrol_manual_action("manual_retry")
    emit_job_status_invalidation(run_id, job_id, job.status, "manual_retry")
    return (
        f"job #{job_id} 已请求立即重试（manual_action=RETRY_NOW，"
        f"failure_streak={job.current_failure_streak or 0}）"
    )


def run_manual_exit_job(
    db: Session,
    params: dict,
    *,
    triggered_by: str,
    requester_user_id: int | None = None,
) -> str:
    from backend.services.plan_run_events import emit_job_status_invalidation
    from backend.services.plan_run_queries import (
        MANUAL_ACTION_JOB_STATUSES,
        load_job_in_run,
    )
    from backend.core.audit import record_audit
    from backend.core.metrics import record_patrol_manual_action

    run_id = params["run_id"]
    job_id = params["job_id"]
    try:
        job = load_job_in_run(db, run_id, job_id)
    except HTTPException as exc:
        raise _http_exception_to_runtime(exc) from exc

    if job.status not in MANUAL_ACTION_JOB_STATUSES:
        raise RuntimeError(
            f"job must be RUNNING for manual exit; current status is {job.status}"
        )

    if job.manual_action == "EXIT_REQUESTED":
        return (
            f"job #{job_id} 已处于 EXIT_REQUESTED（status={job.status}），无需重复操作"
        )

    reason = params["reason"]
    now = datetime.now(timezone.utc)
    job.manual_action = "EXIT_REQUESTED"
    if not job.status_reason:
        job.status_reason = f"patrol_manual_exit_pending: {reason}"
    job.updated_at = now
    db.flush()

    record_audit(
        db,
        action="patrol_manual_exit",
        resource_type="job_instance",
        resource_id=job_id,
        details={
            "plan_run_id": run_id,
            "reason": reason,
            "current_failure_streak": job.current_failure_streak or 0,
            "triggered_by": triggered_by,
            "via": "ai_assistant",
        },
        user_id=requester_user_id,
        username=triggered_by,
    )
    db.commit()
    db.refresh(job)
    record_patrol_manual_action("manual_exit")
    emit_job_status_invalidation(run_id, job_id, job.status, "manual_exit_pending")
    return f"job #{job_id} 已请求退出 patrol（manual_action=EXIT_REQUESTED）"


def _schedule_emit_agent_control(
    host_id: str,
    command: str,
    *,
    payload: dict | None = None,
) -> bool:
    """线程安全：从 SAQ worker 向主循环桥接 Agent 控制下发。

    返回是否**已送达**（#759/#1864）：改用 ack 版 ``call_agent_control_sync``，
    而非 fire-and-forget 的 ``emit_agent_control``——后者丢弃 Future，协程体内
    异常（sio 未初始化/关闭竞态）会静默沉淀为「假成功」。ack 版等待 Agent
    control handler 确认，主循环不可用/超时/异常一律返回 False。

    只能从非主循环线程调用（本函数调用点均为 SAQ worker 线程）。
    """
    from backend.realtime.socketio_server import call_agent_control_sync

    try:
        return call_agent_control_sync(host_id, command, payload=payload or {})
    except Exception:
        logger.exception("agent_control_schedule_failed host=%s command=%s", host_id, command)
        return False


def run_trigger_plan_run_archive(
    db: Session,
    params: dict,
    *,
    triggered_by: str,
    requester_user_id: int | None = None,
) -> str:
    from backend.core.audit import record_audit
    from backend.services.plan_run_scan_scope import (
        build_scan_now_payload,
        classify_recycle_targets,
        iter_plan_run_scan_hosts,
    )

    run_id = params["run_id"]
    pr = db.get(PlanRun, run_id)
    if pr is None:
        raise RuntimeError("plan run not found")

    has_jobs = (
        db.query(JobInstance.id)
        .filter(JobInstance.plan_run_id == run_id)
        .first()
    )
    if not has_jobs:
        raise RuntimeError("no jobs found for this plan run")

    host_rows = iter_plan_run_scan_hosts(db, run_id)
    if not host_rows:
        raise RuntimeError("no jobs found for this plan run")

    # ADR-0038 D5：AI 工具路径不是「显式 admin 触发」，退役主机一律跳过并
    # 如实记入 skipped_retired（不虚报完整）。
    targets, skipped_offline_rows, skipped_retired_rows = classify_recycle_targets(
        host_rows, allow_retired=False,
    )
    skipped = [row["host_id"] for row in skipped_offline_rows]
    skipped_retired = [row["host_id"] for row in skipped_retired_rows]

    triggered: list[str] = []
    dispatch_failed: list[str] = []
    for host_id in targets:
        ok_archive = _schedule_emit_agent_control(
            host_id,
            "archive_now",
            payload={"plan_run_id": run_id},
        )
        ok_scan = _schedule_emit_agent_control(
            host_id,
            "scan_now",
            payload=build_scan_now_payload(db, run_id, host_id, is_final=False),
        )
        if ok_archive and ok_scan:
            triggered.append(host_id)
        else:
            # #759：主循环不可用/入队失败不得报「已触发」——记为下发失败。
            dispatch_failed.append(host_id)

    record_audit(
        db,
        action="ai_assistant_trigger_plan_run_archive",
        resource_type="plan_run",
        resource_id=run_id,
        details={
            "triggered_hosts": triggered,
            "skipped_offline": skipped,
            "skipped_retired": skipped_retired,
            "dispatch_failed": dispatch_failed,
            "triggered_by": triggered_by,
        },
        user_id=requester_user_id,
        username=triggered_by,
    )
    db.commit()
    if dispatch_failed:
        raise RuntimeError(
            f"PlanRun #{run_id} 归档/扫描下发失败：{len(dispatch_failed)} 台 ONLINE "
            f"主机未入队（事件循环不可用？）——{dispatch_failed}"
        )
    return (
        f"PlanRun #{run_id} 归档/扫描已触发：ONLINE {len(triggered)} 台"
        f"{('，跳过 ' + str(len(skipped)) + ' 台离线') if skipped else ''}"
        f"{('，跳过 ' + str(len(skipped_retired)) + ' 台退役（ADR-0038 D5：需显式 admin 触发）') if skipped_retired else ''}"
    )
