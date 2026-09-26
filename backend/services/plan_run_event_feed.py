"""PlanRun 业务流事件源聚合（#1520 垂直切片：plan_runs /events）。

``GET /plan-runs/{id}/events``：trigger / 合成阶段 / step 失败 / log_signal /
audit 融合为 ``EventOut``，再过滤分页。路由退化为
``_require_plan_run`` + ``ok(build_plan_run_events(...))``。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.schemas.plan_run import EventOut, PlanRunEventsOut
from backend.models.audit import AuditLog
from backend.models.host import Device
from backend.models.job import JobInstance, JobLogSignal, StepTrace
from backend.models.plan_run import PlanRun
from backend.core.audit import expand_resource_type_filter
from backend.services.plan_run_read_common import (
    LIVE_PATROL_HEARTBEAT_WINDOW,
    TERMINAL_PR_STATUSES,
    aware,
    iso,
    max_aware_dt,
    min_aware_dt,
    resolve_step_script,
    snapshot_step_scripts,
)

def log_signal_severity(category: str) -> str:
    cat = (category or "").upper()
    if cat in {"AEE", "VENDOR_AEE", "TOMBSTONE"}:
        return "err"
    if cat in {"ANR", "MOBILELOG"}:
        return "warn"
    return "info"


def log_signal_title(category: str) -> str:
    cat = (category or "").upper()
    return {
        "AEE": "AEE Crash 检测",
        "VENDOR_AEE": "Vendor AEE Crash",
        "ANR": "ANR",
        "TOMBSTONE": "Tombstone",
        "MOBILELOG": "MOBILELOG",
    }.get(cat, cat or "异常事件")


def job_has_patrol_signal(job: JobInstance) -> bool:
    return (
        (job.patrol_cycle_count or 0) > 0
        or job.last_patrol_heartbeat_at is not None
        or bool(job.current_patrol_step)
    )


def build_synthetic_stage_events(
    pr: PlanRun,
    jobs: list[JobInstance],
    stage_meta: dict[str, dict[str, object]],
) -> list[EventOut]:
    if not jobs:
        return []

    patrol_jobs = [job for job in jobs if job_has_patrol_signal(job)]
    patrol_cycle_index = max((job.patrol_cycle_count or 0) for job in jobs)
    latest_patrol_heartbeat = max_aware_dt(
        *(job.last_patrol_heartbeat_at for job in jobs)
    )
    patrol_started_at = min_aware_dt(
        stage_meta["patrol"]["started_at"],
        *(job.started_at for job in patrol_jobs),
        latest_patrol_heartbeat,
    )
    live_threshold = datetime.now(timezone.utc) - LIVE_PATROL_HEARTBEAT_WINDOW
    patrol_active_devices = sum(
        1
        for job in jobs
        if job.last_patrol_heartbeat_at
        and aware(job.last_patrol_heartbeat_at) >= live_threshold
    )
    events: list[EventOut] = []
    init_started_at = aware(stage_meta["init"]["started_at"])
    init_ended_at = aware(stage_meta["init"]["ended_at"])
    teardown_started_at = aware(stage_meta["teardown"]["started_at"])
    teardown_ended_at = aware(stage_meta["teardown"]["ended_at"])
    init_has_terminal_success = bool(stage_meta["init"]["has_terminal_success"])
    init_has_failure = bool(stage_meta["init"]["has_failure"])
    teardown_has_terminal_success = bool(stage_meta["teardown"]["has_terminal_success"])
    teardown_has_failure = bool(stage_meta["teardown"]["has_failure"])

    init_completed_at = max_aware_dt(
        init_ended_at,
        init_started_at,
        patrol_started_at,
        pr.started_at,
    )
    if (
        patrol_started_at
        or teardown_started_at
        or (
            pr.status in TERMINAL_PR_STATUSES
            and init_has_terminal_success
            and not init_has_failure
        )
    ):
        events.append(EventOut(
            ts=iso(init_completed_at) or "",
            stage="init",
            severity="ok",
            category="system",
            title="INIT 完成",
            description="已进入后续执行阶段",
            ref={"type": "plan_run", "id": pr.id},
        ))

    if patrol_started_at:
        events.append(EventOut(
            ts=iso(patrol_started_at) or "",
            stage="patrol",
            severity="info",
            category="system",
            title="PATROL 开始",
            description="巡检阶段已启动",
            ref={"type": "plan_run", "id": pr.id},
        ))

    patrol_progress_at = max_aware_dt(
        latest_patrol_heartbeat,
        aware(stage_meta["patrol"]["ended_at"]),
        aware(stage_meta["patrol"]["started_at"]),
        *(job.started_at for job in patrol_jobs),
    )
    if pr.status not in TERMINAL_PR_STATUSES and patrol_cycle_index > 0 and patrol_progress_at:
        heartbeat_window_minutes = int(LIVE_PATROL_HEARTBEAT_WINDOW.total_seconds() // 60)
        patrol_desc = (
            f"最近 {heartbeat_window_minutes} 分钟内 {patrol_active_devices} 台设备上报心跳"
            if patrol_active_devices > 0
            else "已记录巡检进展"
        )
        events.append(EventOut(
            ts=iso(patrol_progress_at) or "",
            stage="patrol",
            severity="info",
            category="system",
            title=f"PATROL 进行中 · 周期 #{patrol_cycle_index}",
            description=patrol_desc,
            ref={"type": "plan_run", "id": pr.id},
        ))

    if teardown_started_at:
        events.append(EventOut(
            ts=iso(teardown_started_at) or "",
            stage="teardown",
            severity="info",
            category="system",
            title="TEARDOWN 开始",
            description="开始收尾清理",
            ref={"type": "plan_run", "id": pr.id},
        ))

    if teardown_ended_at and teardown_has_terminal_success and not teardown_has_failure:
        events.append(EventOut(
            ts=iso(teardown_ended_at) or "",
            stage="teardown",
            severity="ok",
            category="system",
            title="TEARDOWN 完成",
            description="收尾阶段已结束",
            ref={"type": "plan_run", "id": pr.id},
        ))

    return [event for event in events if event.ts]


def build_plan_run_events(
    db: Session,
    pr: PlanRun,
    *,
    stage: str | None = None,
    severity: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> PlanRunEventsOut:
    """ADR-0021/ADR-0022 C5a₂: 业务流事件流聚合（不含 HTTP / 鉴权）。

    融合 trigger / 合成阶段进展 / step_trace 失败 / log_signal / audit_logs。
    facets 基于未过滤全集；列表为过滤 + 分页后的页。
    """
    jobs = db.execute(
        select(JobInstance).where(JobInstance.plan_run_id == pr.id)
    ).scalars().all()
    job_ids = [j.id for j in jobs]
    job_to_device = {j.id: j.device_id for j in jobs}

    devices_by_id: dict[int, str] = {}
    if jobs:
        device_ids = list({j.device_id for j in jobs})
        rows = db.execute(
            select(Device.id, Device.serial).where(Device.id.in_(device_ids))
        ).all()
        devices_by_id = {r.id: r.serial for r in rows}

    stage_meta: dict[str, dict[str, object]] = {
        "init": {
            "started_at": None,
            "ended_at": None,
            "has_terminal_success": False,
            "has_failure": False,
        },
        "patrol": {
            "started_at": None,
            "ended_at": None,
            "has_terminal_success": False,
            "has_failure": False,
        },
        "teardown": {
            "started_at": None,
            "ended_at": None,
            "has_terminal_success": False,
            "has_failure": False,
        },
    }
    if job_ids:
        trace_rows = db.execute(
            select(
                StepTrace.stage,
                StepTrace.event_type,
                StepTrace.status,
                StepTrace.original_ts,
            ).where(StepTrace.job_id.in_(job_ids))
        ).all()
        for trace_stage, event_type, status, original_ts in trace_rows:
            if trace_stage not in stage_meta:
                continue
            bucket = stage_meta[trace_stage]
            started_at = bucket["started_at"]
            ended_at = bucket["ended_at"]
            if started_at is None or original_ts < started_at:
                bucket["started_at"] = original_ts
            if ended_at is None or original_ts > ended_at:
                bucket["ended_at"] = original_ts
            if event_type == "COMPLETED" and status in {"COMPLETED", "SKIPPED"}:
                bucket["has_terminal_success"] = True
            if event_type == "FAILED" or status in {"FAILED", "ABORTED"}:
                bucket["has_failure"] = True

    events: list[EventOut] = []

    # 1) trigger 事件 — PlanRun 自身
    events.append(EventOut(
        ts=iso(pr.started_at) or "",
        stage="trigger",
        severity="ok",
        category="trigger",
        title=f"PlanRun #{pr.id} 启动",
        description=f"触发方式 {pr.run_type}" + (f" · 用户 {pr.triggered_by}" if pr.triggered_by else ""),
        ref={"type": "plan_run", "id": pr.id},
    ))

    # 2) 有限的阶段进展合成事件,避免活跃 patrol 只剩 trigger/异常事件
    events.extend(build_synthetic_stage_events(pr, jobs, stage_meta))

    # 3) step_trace 失败 / abort 事件
    #    v3: 收紧 WHERE — 只取 event_type=FAILED 或 RUN_COMPLETE + terminal status;
    #    区分 ABORTED (warn + "Job 已中止") 与 FAILED (err + "Job 失败")
    #    #3350：step 类事件按 (stage, step_id) 查快照补脚本身份；job 级
    #    （step_id="__job__"）与查不到的键 → None（ADR-0023 D2）
    script_index = snapshot_step_scripts(pr.plan_snapshot)
    if job_ids:
        bad_traces = db.execute(
            select(StepTrace)
            .where(
                (StepTrace.job_id.in_(job_ids))
                & (
                    (StepTrace.event_type == "FAILED")
                    | (
                        (StepTrace.event_type == "RUN_COMPLETE")
                        & (StepTrace.status.in_(["FAILED", "ABORTED"]))
                    )
                )
            )
            .order_by(StepTrace.original_ts.desc())
        ).scalars().all()
        for t in bad_traces:
            dev_id = job_to_device.get(t.job_id)
            is_aborted = (
                t.step_id == "__job__"
                and t.event_type == "RUN_COMPLETE"
                and t.status == "ABORTED"
            )
            is_job_failed = (
                t.step_id == "__job__"
                and t.event_type == "RUN_COMPLETE"
                and t.status == "FAILED"
            )
            if is_aborted:
                title = f"Job #{t.job_id} 已中止"
                evt_severity = "warn"
                evt_stage = "system"
            elif is_job_failed:
                title = f"Job #{t.job_id} 失败"
                evt_severity = "err"
                evt_stage = "system"
            else:
                title = f"{t.stage}.{t.step_id} 失败"
                evt_severity = "err"
                evt_stage = t.stage if t.stage in {"init", "patrol", "teardown"} else "system"
            # #173: 把超时区分透传到前端事件流（exit 124/125 + timeout_kind）。
            timeout_kind = (
                (t.step_metadata or {}).get("timeout_kind")
                if isinstance(t.step_metadata, dict) else None
            )
            if t.exit_code in (124, 125) or timeout_kind:
                details = []
                if t.exit_code is not None:
                    details.append(f"exit={t.exit_code}")
                if timeout_kind:
                    details.append(f"timeout_kind={timeout_kind}")
                suffix = f"[{', '.join(details)}]"
                message_limit = max(0, 512 - len(suffix) - 1)
                description = (
                    f"{(t.error_message or '')[:message_limit]} {suffix}"
                ).strip()
            else:
                description = (t.error_message or "")[:512]
            # #3350（ADR-0023 D2）：只在这一支（category=step）填脚本身份；
            # 其余事件类型（trigger/system/log_signal/audit）保持 None
            script_name, script_version = resolve_step_script(
                script_index, stage=t.stage, step_key=t.step_id,
            )
            events.append(EventOut(
                ts=iso(t.original_ts) or "",
                stage=evt_stage,
                severity=evt_severity,
                category="step",
                title=title,
                description=description,
                job_id=t.job_id,
                device_id=dev_id,
                device_serial=devices_by_id.get(dev_id) if dev_id else None,
                ref={"type": "step_trace", "id": t.id},
                script_name=script_name,
                script_version=script_version,
            ))

    # 4) log_signal 事件(watcher 异常)
    if job_ids:
        signal_rows = db.execute(
            select(JobLogSignal)
            .where(JobLogSignal.job_id.in_(job_ids))
            .order_by(JobLogSignal.detected_at.desc())
        ).scalars().all()
        for s in signal_rows:
            dev_id = job_to_device.get(s.job_id)
            events.append(EventOut(
                ts=iso(s.detected_at) or "",
                stage="patrol",  # watcher signals 都视为 patrol 阶段事件
                severity=log_signal_severity(s.category),
                category="log_signal",
                title=log_signal_title(s.category),
                description=(s.first_lines or "")[:512] or s.path_on_device,
                job_id=s.job_id,
                device_id=dev_id,
                device_serial=devices_by_id.get(dev_id) or s.device_serial,
                ref={"type": "log_signal", "id": s.id},
            ))

    # 5) audit_logs(plan_run / job_instance / dispatch_gate)
    # AuditLog.resource_id 列宽到 String(64) 后(g0b1c2d3e4f5 迁移),需将整型主键
    # 转字符串再比较;PG 严格类型不会做隐式 varchar=int 转换。
    # #2872：筛选值走 expand_resource_type_filter——审计行 append-only（ADR-0015），
    # 09-19 前写入的 `job` 别名行必须与规范值一起被筛到，否则在本视图静默消失。
    job_id_strs = [str(j) for j in (job_ids or [-1])]
    audit_q = select(AuditLog).where(
        (AuditLog.resource_type.in_(expand_resource_type_filter("plan_run"))
         & (AuditLog.resource_id == str(pr.id)))
        | (AuditLog.resource_type.in_(expand_resource_type_filter("job_instance"))
           & AuditLog.resource_id.in_(job_id_strs))
    ).order_by(AuditLog.timestamp.desc())
    for log in db.execute(audit_q).scalars().all():
        sev = "warn" if "abort" in (log.action or "") or "fail" in (log.action or "") else "info"
        events.append(EventOut(
            ts=iso(log.timestamp) or "",
            stage="system",
            severity=sev,
            category="audit",
            title=log.action or "audit",
            description=str(log.details or {})[:512],
            ref={"type": "audit_log", "id": log.id},
        ))

    # 6) facets — 基于全集
    facets_stage: dict[str, int] = {}
    facets_sev:   dict[str, int] = {}
    for e in events:
        facets_stage[e.stage] = facets_stage.get(e.stage, 0) + 1
        facets_sev[e.severity] = facets_sev.get(e.severity, 0) + 1
    facets_stage["all"] = len(events)
    facets_sev["all"]   = len(events)

    # 7) 过滤
    filtered = events
    if stage and stage.lower() != "all":
        filtered = [e for e in filtered if e.stage == stage.lower()]
    if severity and severity.lower() != "all":
        filtered = [e for e in filtered if e.severity == severity.lower()]
    if search and search.strip():
        kw = search.strip().lower()
        filtered = [
            e for e in filtered
            if kw in e.title.lower()
            or kw in (e.description or "").lower()
            or kw in (e.device_serial or "").lower()
        ]

    # 8) 按 ts 倒序 + 分页
    filtered.sort(key=lambda e: e.ts, reverse=True)
    total = len(filtered)
    page = filtered[offset: offset + limit]

    return PlanRunEventsOut(
        plan_run_id=pr.id,
        events=page,
        total=total,
        facets={"by_stage": facets_stage, "by_severity": facets_sev},
    )
