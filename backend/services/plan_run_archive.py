"""PlanRun 手动归档 + scan 触发（#1520 垂直切片：POST /plan-runs/{id}/archive）。

ADR-0025 S2：向涉及 host 下发 ``archive_now`` / ``scan_now``；ADR-0038 D5
退役机仅 admin 可触达并审计。

路由退化为 ``ok(await archive_plan_run_logs(...))``。
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from backend.api.schemas.plan_run import PlanRunArchiveTriggerOut
from backend.services.audit_writer import record_audit
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun
from backend.realtime.socketio_server import emit_agent_control
from backend.services.plan_run_scan_scope import (
    build_scan_now_payload,
    classify_recycle_targets,
    iter_plan_run_scan_hosts,
)


async def archive_plan_run_logs(
    db: Session,
    run_id: int,
    *,
    allow_retired: bool,
    user_id: Optional[int],
    username: Optional[str],
    request: Optional[Request] = None,
) -> PlanRunArchiveTriggerOut:
    """手动触发该 PlanRun 涉及 host 的运行日志立即归档 + scan。

    经 SocketIO control 向各 ONLINE host 的 Agent 下发:
      - archive_now: Agent ``scan_once(grace_seconds=0)``；实际仍受 ``MIN_GRACE_SECONDS``
        （300s）下限约束，并跳过 active Job，防止刚启动 Job 被误 prune。
      - scan_now: 向本 PlanRun 涉及的全部 host 下发同一份设备 serial 列表
        （可跨主机），Agent 只扫本地 HDD 上命中的 ``{folder}/{serial}/``。
    归档和 scan 均为异步——返回「已触发」，前端应轮询/refetch。
    """
    pr = db.get(PlanRun, run_id)
    if pr is None:
        raise HTTPException(status_code=404, detail="plan run not found")

    has_jobs = (
        db.query(JobInstance.id)
        .filter(JobInstance.plan_run_id == run_id)
        .first()
    )
    if not has_jobs:
        raise HTTPException(status_code=400, detail="no jobs found for this plan run")

    host_rows = iter_plan_run_scan_hosts(db, run_id)
    if not host_rows:
        raise HTTPException(status_code=400, detail="no jobs found for this plan run")

    targets, skipped_offline, skipped_retired = classify_recycle_targets(
        host_rows, allow_retired=allow_retired,
    )

    triggered: list[str] = []
    for host_id in targets:
        await emit_agent_control(
            host_id, "archive_now",
            payload={"plan_run_id": run_id},
        )
        await emit_agent_control(
            host_id, "scan_now",
            payload=build_scan_now_payload(db, run_id, host_id, is_final=False),
        )
        triggered.append(host_id)

    record_audit(
        db,
        action="plan_run_archive_scan_trigger",
        resource_type="plan_run",
        resource_id=str(run_id),
        details={
            "triggered_hosts": triggered,
            "skipped_offline": skipped_offline,
            "skipped_retired": skipped_retired,
            "allow_retired": allow_retired,
        },
        user_id=user_id,
        username=username,
        request=request,
    )
    db.commit()

    return PlanRunArchiveTriggerOut(
        plan_run_id=run_id,
        archived_now=True,
        triggered_hosts=triggered,
        skipped_offline=skipped_offline,
        skipped_retired=skipped_retired,
    )
