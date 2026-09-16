"""PlanRun 业务流时间线聚合（#1520 垂直切片：plan_runs timeline）。

``GET /plan-runs/{id}/timeline`` 的 stage/step 聚合、patrol 心跳活跃度与
current_stage 推导。路由退化为 ``_require_plan_run`` + ``ok(build_...)``。

允许 import ``api.schemas``（#1519 门禁只禁止 services → api.routes）。
共享的 ``_aware`` / ``_LIVE_PATROL_*`` 仍留在路由供 events 等端点使用；
本模块内保留同口径副本，避免 timeline↔events 反向耦合。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.api.schemas.plan_run import (
    PlanRunTimelineOut,
    StageOut,
    StageStepOut,
)
from backend.models.enums import JobStatus, PlanRunStatus
from backend.models.job import JobInstance, StepTrace
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun

_LIVE_PATROL_HEARTBEAT_WINDOW = timedelta(seconds=180)
_TERMINAL_PR_STATUSES = {
    PlanRunStatus.SUCCESS.value,
    PlanRunStatus.PARTIAL_SUCCESS.value,
    PlanRunStatus.FAILED.value,
}


def _aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _iso(v) -> str | None:
    if v is None:
        return None
    return v.isoformat()


def _duration_seconds(start, end) -> float | None:
    if start is None:
        return None
    if end is None:
        end = datetime.now(timezone.utc)
    try:
        return max(0.0, (_aware(end) - _aware(start)).total_seconds())
    except TypeError:
        return None


def _stage_status_from_steps(
    stage: str,
    *,
    pr_status: str,
    job_total: int,
    succeeded: int,
    failed: int,
    has_running_jobs: bool,
) -> str:
    """从 step_trace 聚合派生 stage 整体状态。

    init 阶段:任意 step 失败 → failed;全部 step ok 且 patrol 已启动 → completed;否则 running/pending
    teardown 阶段:终态 PlanRun 时若有 step_trace → completed; 否则 pending/skipped(manual_exit)
    patrol 阶段:running 时 → running; 终态时 → completed (或 failed)
    """
    if pr_status in _TERMINAL_PR_STATUSES:
        if stage == "teardown":
            if succeeded == 0 and failed == 0:
                return "skipped"
            return "completed" if failed == 0 else "failed"
        return "completed" if failed == 0 else "failed"
    # PlanRun RUNNING
    if stage == "init":
        if failed > 0:
            return "failed"
        if succeeded >= job_total:
            return "completed"
        return "running" if has_running_jobs else "pending"
    if stage == "patrol":
        if succeeded == 0 and failed == 0 and not has_running_jobs:
            return "pending"
        return "running"
    return "pending"  # teardown 在 RUNNING 时永远是 pending


def build_plan_run_timeline(db: Session, pr: PlanRun) -> PlanRunTimelineOut:
    """ADR-0021/ADR-0022 C5a₂: 业务流时间线聚合（不含 HTTP / 鉴权）。

    patrol 阶段额外暴露 patrol_cycle_index / active_devices（活跃心跳窗口内）,
    数据源是 JobInstance.patrol_*_cycle_count（ADR-0022 心跳聚合,而非 step_trace）。
    """
    plan = db.get(Plan, pr.plan_id)

    jobs = db.execute(
        select(JobInstance).where(JobInstance.plan_run_id == pr.id)
    ).scalars().all()
    job_total = len(jobs)
    job_ids = [j.id for j in jobs]
    has_running = any(j.status == JobStatus.RUNNING.value for j in jobs)

    # 1) 静态 step 定义(从 plan_snapshot 或 PlanStep)
    snapshot_steps: list[dict] = []
    if isinstance(pr.plan_snapshot, dict):
        snapshot_steps = pr.plan_snapshot.get("steps") or []
    if not snapshot_steps and pr.plan_id:
        rows = db.execute(
            select(PlanStep)
            .where(PlanStep.plan_id == pr.plan_id)
            .order_by(PlanStep.stage, PlanStep.sort_order)
        ).scalars().all()
        snapshot_steps = [
            {
                "step_key": s.step_key,
                "script_name": s.script_name,
                "stage": s.stage,
                "sort_order": s.sort_order,
            }
            for s in rows
        ]

    # 2) 聚合 step_trace:按 (stage, step_id, event_type, status) 分桶
    #    v3: 基于真实 (event_type, status) 组合; 排除 STARTED 中间事件
    step_agg: dict[tuple[str, str], dict[str, int]] = {}
    if job_ids:
        agg_rows = db.execute(
            select(
                StepTrace.stage,
                StepTrace.step_id,
                StepTrace.event_type,
                StepTrace.status,
                func.count(StepTrace.id),
            )
            .where(
                StepTrace.job_id.in_(job_ids),
                StepTrace.event_type != "STARTED",
            )
            .group_by(
                StepTrace.stage, StepTrace.step_id,
                StepTrace.event_type, StepTrace.status,
            )
        ).all()
        for stage, step_id, event_type, status, cnt in agg_rows:
            key = (stage, step_id)
            bucket = step_agg.setdefault(
                key, {"succeeded": 0, "failed": 0, "skipped": 0},
            )
            if event_type == "COMPLETED" and status == "COMPLETED":
                bucket["succeeded"] += cnt
            elif event_type == "COMPLETED" and status == "SKIPPED":
                bucket["skipped"] += cnt
            elif event_type == "FAILED" or status == "FAILED":
                bucket["failed"] += cnt
            # event_type=RUN_COMPLETE + step_id=__job__: step_id 不在
            # plan_snapshot, 不会被纳入 stage 桶, 无需特殊处理

    # 3) 按 stage 组织 steps;按 plan_snapshot 顺序保留
    stages_def: dict[str, list[StageStepOut]] = {"init": [], "patrol": [], "teardown": []}
    for s in snapshot_steps:
        stage = s.get("stage")
        if stage not in stages_def:
            continue
        key = (stage, s.get("step_key", ""))
        agg = step_agg.get(key, {"succeeded": 0, "failed": 0, "skipped": 0})
        succeeded = agg["succeeded"]
        failed = agg["failed"]
        skipped = agg["skipped"]
        running = max(0, job_total - succeeded - failed - skipped) if has_running else 0
        stages_def[stage].append(StageStepOut(
            step_key=s.get("step_key", ""),
            script_name=s.get("script_name", ""),
            stage=stage,
            sort_order=int(s.get("sort_order", 0)),
            device_total=job_total,
            device_succeeded=succeeded,
            device_failed=failed,
            device_skipped=skipped,
            device_running=running,
        ))

    # 4) patrol 心跳聚合
    patrol_cycle_index = None
    patrol_active = None
    if jobs:
        cycles = [j.patrol_cycle_count or 0 for j in jobs]
        patrol_cycle_index = max(cycles) if cycles else 0
        live_threshold = datetime.now(timezone.utc) - _LIVE_PATROL_HEARTBEAT_WINDOW
        patrol_active = sum(
            1 for j in jobs
            if j.last_patrol_heartbeat_at
            and _aware(j.last_patrol_heartbeat_at) >= live_threshold
        )

    # 5) Stage counts are device counts, not step execution counts.  A device
    # succeeds only after every enabled step in that stage has a successful
    # terminal trace.
    required_steps_by_stage: dict[str, set[str]] = {
        stage: {
            str(step.get("step_key", ""))
            for step in snapshot_steps
            if step.get("stage") == stage
            and step.get("enabled", True) is not False
        }
        for stage in ("init", "patrol", "teardown")
    }
    job_step_results: dict[tuple[int, str, str], set[str]] = {}
    if job_ids:
        result_rows = db.execute(
            select(
                StepTrace.job_id,
                StepTrace.stage,
                StepTrace.step_id,
                StepTrace.event_type,
                StepTrace.status,
            ).where(
                StepTrace.job_id.in_(job_ids),
                StepTrace.event_type != "STARTED",
            )
        ).all()
        for job_id, stage, step_id, event_type, status in result_rows:
            result = None
            if event_type == "COMPLETED" and status == "COMPLETED":
                result = "succeeded"
            elif event_type == "COMPLETED" and status == "SKIPPED":
                result = "skipped"
            elif event_type == "FAILED" or status == "FAILED":
                result = "failed"
            if result:
                job_step_results.setdefault(
                    (job_id, stage, step_id), set(),
                ).add(result)

    def _stage_device_counts(stage_name: str) -> tuple[int, int, int]:
        required = required_steps_by_stage[stage_name]
        if not required:
            return 0, 0, 0
        succeeded = failed = skipped = 0
        for job in jobs:
            results = {
                step_id: job_step_results.get(
                    (job.id, stage_name, step_id), set(),
                )
                for step_id in required
            }
            if any("failed" in values and "succeeded" not in values for values in results.values()):
                failed += 1
            elif all("succeeded" in values for values in results.values()):
                succeeded += 1
            elif all(values & {"succeeded", "skipped"} for values in results.values()):
                skipped += 1
        return succeeded, failed, skipped

    # 6) 每 stage 的 started_at / ended_at — 取该 stage 第一个/最后一个 step_trace
    stage_ts: dict[str, dict[str, Optional[datetime]]] = {
        "init": {"started_at": None, "ended_at": None},
        "patrol": {"started_at": None, "ended_at": None},
        "teardown": {"started_at": None, "ended_at": None},
    }
    if job_ids:
        ts_rows = db.execute(
            select(
                StepTrace.stage,
                func.min(StepTrace.original_ts),
                func.max(StepTrace.original_ts),
            )
            .where(StepTrace.job_id.in_(job_ids))
            .group_by(StepTrace.stage)
        ).all()
        for stage, ts_min, ts_max in ts_rows:
            if stage in stage_ts:
                stage_ts[stage]["started_at"] = ts_min
                stage_ts[stage]["ended_at"]   = ts_max

    # 7) current_stage 推导
    current_stage = "pending"
    if pr.status in _TERMINAL_PR_STATUSES:
        current_stage = "done"
    elif stage_ts["teardown"]["started_at"]:
        current_stage = "teardown"
    elif stage_ts["patrol"]["started_at"] or patrol_cycle_index:
        current_stage = "patrol"
    elif stage_ts["init"]["started_at"]:
        current_stage = "init"

    stages_out: list[StageOut] = []
    for stage_name in ("init", "patrol", "teardown"):
        steps = stages_def.get(stage_name, [])
        succeeded, failed, skipped = _stage_device_counts(stage_name)
        st = _stage_status_from_steps(
            stage_name,
            pr_status=pr.status,
            job_total=job_total,
            succeeded=succeeded,
            failed=failed,
            has_running_jobs=has_running,
        )
        s_at = stage_ts[stage_name]["started_at"]
        e_at = stage_ts[stage_name]["ended_at"] if st in {"completed", "failed", "skipped"} else None
        stage_obj = StageOut(
            stage=stage_name,
            status=st,
            started_at=_iso(s_at),
            ended_at=_iso(e_at),
            duration_seconds=_duration_seconds(s_at, e_at),
            device_total=job_total,
            device_succeeded=succeeded,
            device_failed=failed,
            device_skipped=skipped,
            steps=steps,
        )
        if stage_name == "patrol":
            stage_obj.patrol_cycle_index = patrol_cycle_index
            stage_obj.patrol_active_devices = patrol_active
            snapshot_plan = (
                (pr.plan_snapshot or {}).get("plan", {})
                if isinstance(pr.plan_snapshot, dict)
                else {}
            )
            stage_obj.patrol_interval_seconds = snapshot_plan.get(
                "patrol_interval_seconds",
            )
        stages_out.append(stage_obj)

    # 刻意读时重数而非读 plan_run.aborted_job_count 计数列（跨区收口链 A F-A4 裁决：
    # 保留重数并成文）——timeline 需要读取时刻的准确值，而计数列在 abort 竞态下
    # 可能滞后（#1552：expire 时序与聚合读取的竞态窗口）；本端点已加载 jobs，
    # 重数无额外查询。聚合权威仍是计数列（O(1)），两源瞬态分叉可接受，勿在未
    # 复核 #1552 竞态语义前把此处改成读计数列。
    aborted_job_count = sum(1 for j in jobs if j.status == JobStatus.ABORTED.value)

    return PlanRunTimelineOut(
        plan_run_id=pr.id,
        current_stage=current_stage,
        stages=stages_out,
        aborted_job_count=aborted_job_count,
        triggered_at=_iso(pr.started_at) or "",
        triggered_by=pr.triggered_by,
        run_type=pr.run_type,
        plan_name=plan.name if plan else None,
    )
