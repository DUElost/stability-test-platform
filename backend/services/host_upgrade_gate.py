"""统一升级门禁：活跃 Job 拒绝 / abort 排空 / 维护窗口持有（#1249）。

Why: ADR-0021 D7/D8 的「先排空再升级」协议此前只完整实现在 UI 热更新路由里；
Ansible `update_agent.yml` 直接 rsync + 重启（无门禁、无 abort、不持窗口），
`batch_hot_update.py --direct` 也没有 abort 能力。维护窗口
（`host.maintenance_until`）是唯一能同时挡住派发与 claim 的互斥面（#960），
本模块把门禁与窗口收敛成一处实现，各入口共用：

    gate = begin_host_upgrade(db, host_id, holder=..., abort_running_jobs=...)
    try:
        ... rsync / restart ...
    finally:
        end_host_upgrade(db, host_id, gate["holder"])

异常只表达拒绝原因（不掺 HTTP 语义），由路由 / CLI / playbook 各自映射；
窗口释放失败靠 TTL 过期兜底（host_maintenance 既有设计）。
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from backend.models.enums import JobStatus
from backend.models.host import Host
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun
from backend.services.host_maintenance import (
    HostMaintenanceConflict,
    acquire_maintenance_window,
    release_maintenance_window,
)
from backend.services.plan_run_abort import abort_jobs_for_host

logger = logging.getLogger(__name__)

# 与 plan_dispatcher_sync.ACTIVE_JOB_STATUSES 保持一致：UNKNOWN grace 期内
# 作业仍可能恢复（UNKNOWN→RUNNING），升级门禁视为活跃（#134）。
ACTIVE_JOB_STATUSES = (
    JobStatus.PENDING.value,
    JobStatus.RUNNING.value,
    JobStatus.UNKNOWN.value,
)

# 排空轮询参数沿用既有 UI 热更新 env 名，迁移期不改行为（#1249）。
ABORT_POLL_TIMEOUT_SECONDS = float(
    os.getenv("HOT_UPDATE_ABORT_POLL_TIMEOUT_SECONDS", "45")
)
ABORT_POLL_INTERVAL_SECONDS = float(
    os.getenv("HOT_UPDATE_ABORT_POLL_INTERVAL_SECONDS", "1.0")
)


class HostUpgradeGateError(RuntimeError):
    """升级门禁拒绝基类；``code`` 供各入口映射状态码/退出原因。"""

    code = "UPGRADE_GATE_ERROR"

    def __init__(self, message: str, *, detail: Optional[dict] = None) -> None:
        super().__init__(message)
        self.detail = detail or {}


class HostNotFoundError(HostUpgradeGateError):
    code = "HOST_NOT_FOUND"


class HostHasActiveJobsError(HostUpgradeGateError):
    code = "HOST_HAS_ACTIVE_JOBS"

    def __init__(self, active_jobs: list[dict]) -> None:
        super().__init__(
            f"Host has {len(active_jobs)} active job(s) and abort was not requested",
            detail={"active_jobs": active_jobs},
        )
        self.active_jobs = active_jobs


class HostAbortPendingError(HostUpgradeGateError):
    """所有活跃 Job 都已在 abort 收口中，等 reaper 收割完再重试。"""

    code = "HOST_ABORT_PENDING"

    def __init__(self, active_jobs: list[dict], retry_after_seconds: int) -> None:
        super().__init__(
            f"Abort is still draining for {len(active_jobs)} job(s)",
            detail={
                "active_jobs": active_jobs,
                "retry_after_seconds": retry_after_seconds,
            },
        )
        self.active_jobs = active_jobs
        self.retry_after_seconds = retry_after_seconds


class HostAbortDrainTimeoutError(HostUpgradeGateError):
    code = "ABORT_DRAIN_TIMEOUT"

    def __init__(self, lingering_jobs: list[int], abort_summary: Optional[dict]) -> None:
        super().__init__(
            f"{len(lingering_jobs)} job(s) did not reach a terminal state "
            "after abort",
            detail={
                "lingering_jobs": lingering_jobs,
                "abort_summary": abort_summary,
            },
        )
        self.lingering_jobs = lingering_jobs
        self.abort_summary = abort_summary


def active_jobs_for_host(db: Session, host_id: str) -> list[JobInstance]:
    return (
        db.query(JobInstance)
        .filter(
            JobInstance.host_id == host_id,
            JobInstance.status.in_(ACTIVE_JOB_STATUSES),
        )
        .order_by(JobInstance.id)
        .all()
    )


def _abort_pending_ids(db: Session, rows: list[JobInstance]) -> set[int]:
    """返回 run_context 已带 ``abort_requested`` 的 Job id 集合。"""
    pr_ids = {j.plan_run_id for j in rows if j.plan_run_id is not None}
    if not pr_ids:
        return set()
    pr_map = {
        pr.id: pr
        for pr in db.query(PlanRun).filter(PlanRun.id.in_(pr_ids)).all()
    }
    pending: set[int] = set()
    for job in rows:
        pr = pr_map.get(job.plan_run_id)
        if pr is None or not isinstance(pr.run_context, dict):
            continue
        if "abort_requested" in pr.run_context:
            pending.add(job.id)
    return pending


def _abort_pending_retry_after(
    db: Session, rows: list[JobInstance], pending_ids: set[int]
) -> int:
    """按最晚 abort 的 Job 剩余 grace 估算重试秒数（与 UI 热更新一致）。"""
    from backend.scheduler.app_scheduler import RECONCILER_INTERVAL
    from backend.scheduler.device_lease_reconciler import _ABORT_REAPER_GRACE_SECONDS

    pr_ids = {j.plan_run_id for j in rows if j.id in pending_ids and j.plan_run_id}
    pr_map = (
        {pr.id: pr for pr in db.query(PlanRun).filter(PlanRun.id.in_(pr_ids)).all()}
        if pr_ids
        else {}
    )
    max_remaining = 0.0
    now = datetime.now(timezone.utc)
    for job in rows:
        if job.id not in pending_ids:
            continue
        pr = pr_map.get(job.plan_run_id)
        if pr is None or not isinstance(pr.run_context, dict):
            continue
        at_str = pr.run_context.get("abort_requested", {}).get("at", "")
        if not at_str:
            continue
        try:
            at_dt = datetime.fromisoformat(str(at_str).replace("Z", "+00:00"))
            elapsed = (now - at_dt).total_seconds()
        except (ValueError, TypeError):
            continue
        max_remaining = max(
            max_remaining, max(0.0, _ABORT_REAPER_GRACE_SECONDS - elapsed)
        )
    return int(max_remaining + RECONCILER_INTERVAL)


def wait_until_no_active_jobs(
    db: Session,
    host_id: str,
    *,
    timeout_seconds: Optional[float] = None,
    poll_interval_seconds: Optional[float] = None,
) -> tuple[bool, list[int]]:
    """轮询到该主机零活跃 Job 或超时；返回 (ok, lingering_job_ids)。"""
    timeout = (
        ABORT_POLL_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    )
    interval = (
        ABORT_POLL_INTERVAL_SECONDS
        if poll_interval_seconds is None
        else poll_interval_seconds
    )
    deadline = time.monotonic() + timeout
    while True:
        db.expire_all()
        lingering = [
            j.id for j in active_jobs_for_host(db, host_id)
        ]
        if not lingering:
            return True, []
        if time.monotonic() >= deadline:
            return False, lingering
        time.sleep(interval)


def begin_host_upgrade(
    db: Session,
    host_id: str,
    *,
    holder: str,
    abort_running_jobs: bool = False,
    abort_reason: str = "aborted_for_host_update",
    triggered_by: str = "upgrade_gate",
    audit_user_id: Optional[int] = None,
    audit_username: Optional[str] = None,
    drain_timeout_seconds: Optional[float] = None,
) -> dict[str, Any]:
    """通过门禁并持有维护窗口；拒绝原因以异常抛出。

    成功返回：host_id / holder / expires_at / active_jobs（升级前快照）/
    aborted_summary（未走 abort 时为 None）。
    """
    host = db.get(Host, host_id)
    if host is None:
        raise HostNotFoundError(f"host {host_id} not found")

    rows = active_jobs_for_host(db, host_id)
    pending_ids = _abort_pending_ids(db, rows)
    active_summary = [
        {
            "id": j.id,
            "plan_run_id": j.plan_run_id,
            "plan_id": j.plan_id,
            "device_id": j.device_id,
            "status": j.status,
            "abort_pending": j.id in pending_ids,
        }
        for j in rows
    ]
    aborted_summary: Optional[dict] = None

    if active_summary:
        if not abort_running_jobs:
            if len(pending_ids) == len(active_summary):
                raise HostAbortPendingError(
                    active_summary,
                    _abort_pending_retry_after(db, rows, pending_ids),
                )
            raise HostHasActiveJobsError(active_summary)

        aborted_summary = abort_jobs_for_host(
            host_id,
            db=db,
            reason=abort_reason,
            triggered_by=triggered_by,
            audit_user_id=audit_user_id,
            audit_username=audit_username,
        )
        logger.info(
            "upgrade_gate_abort_initiated host=%s holder=%s plan_runs=%s jobs=%s",
            host_id,
            holder,
            aborted_summary["plan_runs"],
            aborted_summary["aborted_jobs"],
        )
        ok_drained, lingering = wait_until_no_active_jobs(
            db, host_id, timeout_seconds=drain_timeout_seconds
        )
        if not ok_drained:
            raise HostAbortDrainTimeoutError(lingering, aborted_summary)

    if not acquire_maintenance_window(db, host_id, holder):
        raise HostMaintenanceConflict(
            f"host {host_id} already in maintenance window"
        )

    expires_at = host.maintenance_until
    logger.info(
        "upgrade_gate_acquired host=%s holder=%s abort_running_jobs=%s "
        "active_before=%d aborted_jobs=%s expires_at=%s",
        host_id,
        holder,
        abort_running_jobs,
        len(active_summary),
        aborted_summary["aborted_jobs"] if aborted_summary else [],
        expires_at.isoformat() if expires_at else None,
    )
    return {
        "host_id": host_id,
        "holder": holder,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "active_jobs": active_summary,
        "aborted_summary": aborted_summary,
    }


def end_host_upgrade(db: Session, host_id: str, holder: str) -> None:
    """释放维护窗口（只在 holder 匹配时生效，见 host_maintenance）。"""
    release_maintenance_window(db, host_id, holder)
    logger.info("upgrade_gate_released host=%s holder=%s", host_id, holder)
