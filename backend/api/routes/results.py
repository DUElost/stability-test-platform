# -*- coding: utf-8 -*-
"""
Results summary API — aggregated test run statistics for the dashboard.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from backend.api.routes.auth import get_current_active_user, User
from backend.core.database import get_db
from backend.models.job import JobInstance, StepTrace
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.project import TestProject
from backend.services.log_observation import aggregate_risk_summary

router = APIRouter(prefix="/api/v1/results", tags=["results"])


# ---------- Response schemas ----------

class RunsByStatus(BaseModel):
    finished: int = 0
    failed: int = 0
    canceled: int = 0
    running: int = 0
    total: int = 0


class TestTypeStat(BaseModel):
    type: str
    finished: int = 0
    failed: int = 0
    total: int = 0


class RiskDistribution(BaseModel):
    high: int = 0
    medium: int = 0
    low: int = 0
    unknown: int = 0


class RecentRun(BaseModel):
    run_id: int
    task_name: str
    task_type: str
    status: str
    risk_level: str = "UNKNOWN"
    # ADR-0029：归属项目（plan_run 快照，F2 口径）
    project_key: Optional[str] = None
    duration_seconds: Optional[float] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class ResultsSummary(BaseModel):
    runs_by_status: RunsByStatus
    test_type_stats: List[TestTypeStat]
    risk_distribution: RiskDistribution
    recent_runs: List[RecentRun]


class RiskTrendBucket(BaseModel):
    """单日 S/A/B 风险计数（项目维度，P2）。"""

    date: str  # YYYY-MM-DD（run 起始日）
    S: int = 0
    A: int = 0
    B: int = 0
    runs: int = 0


class RiskTrendOut(BaseModel):
    """项目级风险趋势——按天的 S/A/B 计数。

    数据源：DLE 权威聚合（aggregate_risk_summary，与 /results 同口径），
    run 级风险按 plan_run.started_at 归日。plan_run.project_id 快照过滤
    （P0-1 起新 Run 有真实项目归属）。
    """

    project_key: Optional[str] = None
    days: int
    buckets: List[RiskTrendBucket] = []


# ---------- Helpers ----------

_JOB_STATUS_TO_RUN_STATUS = {
    "PENDING": "QUEUED",
    "RUNNING": "RUNNING",
    "COMPLETED": "FINISHED",
    "FAILED": "FAILED",
    "ABORTED": "CANCELED",
    "UNKNOWN": "RUNNING",
}


def _normalize_job_status(job_status: Any) -> str:
    raw = str(job_status or "").upper()
    return _JOB_STATUS_TO_RUN_STATUS.get(raw, raw or "RUNNING")


def _safe_json_loads(payload: Optional[str]) -> Dict[str, Any]:
    if not payload:
        return {}
    try:
        decoded = json.loads(payload)
    except Exception:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _extract_log_summary_from_snapshot(snapshot_output: Optional[str]) -> Optional[str]:
    payload = _safe_json_loads(snapshot_output)
    update = payload.get("update")
    if not isinstance(update, dict):
        return None
    log_summary = update.get("log_summary")
    return log_summary if isinstance(log_summary, str) else None


def _parse_risk_level(log_summary: Optional[str]) -> str:
    """Extract risk level from log_summary (format: risk=HIGH;...)."""
    if not log_summary:
        return "UNKNOWN"
    for part in log_summary.split(";"):
        part = part.strip()
        if part.lower().startswith("risk="):
            level = part.split("=", 1)[1].strip().upper()
            if level in ("HIGH", "MEDIUM", "LOW"):
                return level
    return "UNKNOWN"


# ---------- Endpoint ----------

@router.get("/summary", response_model=ResultsSummary)
def get_results_summary(
    limit: int = Query(20, ge=1, le=100, description="Number of recent runs"),
    project_key: Optional[str] = Query(None, description="ADR-0029: filter by project key"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> ResultsSummary:
    """Return aggregated test run statistics."""

    def _empty_summary() -> ResultsSummary:
        return ResultsSummary(
            runs_by_status=RunsByStatus(),
            test_type_stats=[],
            risk_distribution=RiskDistribution(),
            recent_runs=[],
        )

    def _is_missing_orchestration_table(exc: Exception) -> bool:
        message = str(exc).lower()
        table_hit = any(
            t in message for t in (
                "job_instance",
                "step_trace",
                "plan_run",
                "plan",
            )
        )
        return (
            table_hit and (
                "does not exist" in message
                or "undefinedtable" in message
                or "no such table" in message
                or "不存在" in message
            )
        )

    try:

        # ADR-0029 P2：project_key 可选过滤（D9 挂起，缺省 = 全量）。
        # 过滤统一经 PlanRun.project_id——Plan.project_id 是可变当前归属，
        # 历史 Run 的归属以 plan_run 快照为准（D5 快照语义；M-b 已冻结）。
        # JobInstance 无 project 列，经 plan_run_id 一次 join 即可。
        target_project_id: Optional[int] = None
        if project_key:
            project = (
                db.query(TestProject)
                .filter(TestProject.project_key == project_key)
                .first()
            )
            if project is None:
                raise HTTPException(status_code=404, detail="project not found")
            target_project_id = project.id

        def _scope_by_project(query):
            """按 project 过滤（若指定）；调用方须已 join PlanRun。"""
            if target_project_id is not None:
                query = query.filter(PlanRun.project_id == target_project_id)
            return query

        # --- runs_by_status (新链路：JobInstance) ---
        status_query = db.query(JobInstance.status, func.count(JobInstance.id))
        if target_project_id is not None:
            status_query = status_query.join(PlanRun, JobInstance.plan_run_id == PlanRun.id)
        status_query = _scope_by_project(status_query)
        status_counts = status_query.group_by(JobInstance.status).all()
        runs_by_status = RunsByStatus()
        for raw_status, count in status_counts:
            normalized = _normalize_job_status(raw_status)
            cnt = int(count or 0)
            runs_by_status.total += cnt
            if normalized == "FINISHED":
                runs_by_status.finished += cnt
            elif normalized == "FAILED":
                runs_by_status.failed += cnt
            elif normalized == "CANCELED":
                runs_by_status.canceled += cnt
            else:
                # QUEUED/RUNNING/UNKNOWN 都视作运行态
                runs_by_status.running += cnt

        # --- test_type_stats (按 Plan.name 聚合) ---
        type_query = db.query(
            Plan.name,
            JobInstance.status,
            func.count(JobInstance.id),
        ).join(Plan, JobInstance.plan_id == Plan.id)
        # 按 Plan.name 分组需保留 Plan join；project 过滤额外 join PlanRun（快照语义）
        if target_project_id is not None:
            type_query = type_query.join(PlanRun, JobInstance.plan_run_id == PlanRun.id)
        type_query = _scope_by_project(type_query)
        type_rows = type_query.group_by(Plan.name, JobInstance.status).all()
        type_agg: Dict[str, Dict[str, int]] = {}
        for template_name, raw_status, cnt in type_rows:
            stat_type = str(template_name or "UNKNOWN")
            bucket = type_agg.setdefault(stat_type, {"finished": 0, "failed": 0, "total": 0})
            count_i = int(cnt or 0)
            bucket["total"] += count_i
            normalized = _normalize_job_status(raw_status)
            if normalized == "FINISHED":
                bucket["finished"] += count_i
            elif normalized == "FAILED":
                bucket["failed"] += count_i
        test_type_stats = [TestTypeStat(type=t, **counts) for t, counts in sorted(type_agg.items())]

        # --- recent_runs (ADR-0020: Plan-based) ---
        # Plan join 供 name 展示；outerjoin PlanRun+TestProject 取归属 key
        # （快照语义）；project 过滤走 _scope_by_project（filter 复用已 join 表）。
        recent_query = (
            db.query(JobInstance, Plan.name, TestProject.project_key)
            .join(Plan, JobInstance.plan_id == Plan.id)
            .outerjoin(PlanRun, JobInstance.plan_run_id == PlanRun.id)
            .outerjoin(TestProject, PlanRun.project_id == TestProject.id)
            .order_by(JobInstance.id.desc())
        )
        recent_query = _scope_by_project(recent_query)
        recent_rows = recent_query.limit(limit).all()
        recent_job_ids = [job.id for job, _plan_name, _project_key in recent_rows]
        snapshot_rows = []
        if recent_job_ids:
            snapshot_rows = (
                db.query(StepTrace.job_id, StepTrace.output)
                .filter(
                    StepTrace.job_id.in_(recent_job_ids),
                    StepTrace.step_id == "__job__",
                    StepTrace.event_type == "RUN_COMPLETE",
                )
                .all()
            )
        snapshot_map = {int(job_id): output for job_id, output in snapshot_rows}

        recent_runs: List[RecentRun] = []
        for job, plan_name, project_key in recent_rows:
            log_summary = _extract_log_summary_from_snapshot(snapshot_map.get(job.id))
            risk = _parse_risk_level(log_summary)
            duration = None
            if job.started_at and job.ended_at:
                duration = (job.ended_at - job.started_at).total_seconds()
            plan_name_norm = str(plan_name or "unknown")
            recent_runs.append(
                RecentRun(
                    run_id=job.id,
                    task_name=plan_name_norm,
                    task_type=plan_name_norm,
                    status=_normalize_job_status(job.status),
                    risk_level=risk,
                    project_key=project_key,
                    duration_seconds=duration,
                    started_at=job.started_at,
                    finished_at=job.ended_at,
                )
            )

        # --- risk_distribution ---
        total_jobs_query = db.query(func.count(JobInstance.id))
        if target_project_id is not None:
            total_jobs_query = total_jobs_query.join(PlanRun, JobInstance.plan_run_id == PlanRun.id)
        total_jobs_query = _scope_by_project(total_jobs_query)
        total_jobs = int(total_jobs_query.scalar() or 0)
        risk_counts = {"high": 0, "medium": 0, "low": 0, "unknown": 0}
        if total_jobs > 0:
            snapshot_query = (
                db.query(StepTrace.job_id, StepTrace.output)
                .join(JobInstance, StepTrace.job_id == JobInstance.id)
                .filter(
                    StepTrace.step_id == "__job__",
                    StepTrace.event_type == "RUN_COMPLETE",
                )
            )
            if target_project_id is not None:
                snapshot_query = snapshot_query.join(PlanRun, JobInstance.plan_run_id == PlanRun.id)
            snapshot_query = _scope_by_project(snapshot_query)
            all_snapshot_rows = snapshot_query.all()
            seen_jobs = set()
            for job_id, output in all_snapshot_rows:
                if job_id in seen_jobs:
                    continue
                seen_jobs.add(job_id)
                level = _parse_risk_level(_extract_log_summary_from_snapshot(output))
                if level == "HIGH":
                    risk_counts["high"] += 1
                elif level == "MEDIUM":
                    risk_counts["medium"] += 1
                elif level == "LOW":
                    risk_counts["low"] += 1
                else:
                    risk_counts["unknown"] += 1
            missing_snapshot_jobs = max(total_jobs - len(seen_jobs), 0)
            risk_counts["unknown"] += missing_snapshot_jobs

        runs_by_status = RunsByStatus(
            finished=int(runs_by_status.finished),
            failed=int(runs_by_status.failed),
            canceled=int(runs_by_status.canceled),
            running=int(runs_by_status.running),
            total=int(runs_by_status.total),
        )

        return ResultsSummary(
            runs_by_status=runs_by_status,
            test_type_stats=test_type_stats,
            risk_distribution=RiskDistribution(**risk_counts),
            recent_runs=recent_runs,
        )
    except ProgrammingError as exc:
        if not _is_missing_orchestration_table(exc):
            raise
        db.rollback()
        return _empty_summary()


@router.get("/risk-trend", response_model=RiskTrendOut)
def get_risk_trend(
    project_key: Optional[str] = Query(
        None, description="ADR-0029 P2: filter by project key（缺省 = 全量）"
    ),
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """项目级风险趋势：按天的 S/A/B 计数（run 级 DLE 权威聚合）。

    过滤统一经 PlanRun.project_id 快照（与 /summary 同口径，P0-1 后新 Run
    有真实项目归属）。run 的风险 = 其全部 job 的 aggregate_risk_summary，
    按 run 起始日归桶。project_key 未知 → 404（与 /summary 同语义）。
    """
    target_project_id: Optional[int] = None
    if project_key:
        project = (
            db.query(TestProject)
            .filter(TestProject.project_key == project_key)
            .first()
        )
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        target_project_id = project.id

    since = datetime.now(timezone.utc) - timedelta(days=days)
    run_query = db.query(PlanRun).filter(
        PlanRun.started_at >= since,
        # 终态才有风险判定；枚举为 SUCCESS/PARTIAL_SUCCESS/FAILED
        PlanRun.status.in_(("SUCCESS", "PARTIAL_SUCCESS", "FAILED")),
    )
    if target_project_id is not None:
        run_query = run_query.filter(PlanRun.project_id == target_project_id)

    buckets: Dict[str, Dict[str, int]] = {}
    for run in run_query.all():
        job_ids = [
            jid for (jid,) in db.query(JobInstance.id)
            .filter(JobInstance.plan_run_id == run.id)
            .all()
        ]
        summary = aggregate_risk_summary(db, job_ids) or {}
        level = str(summary.get("risk_level", "B"))
        if run.started_at is None:
            continue
        day = run.started_at.date().isoformat()
        bucket = buckets.setdefault(day, {"S": 0, "A": 0, "B": 0, "runs": 0})
        if level in ("S", "A", "B"):
            bucket[level] += 1
        bucket["runs"] += 1

    return RiskTrendOut(
        project_key=project_key,
        days=days,
        buckets=[RiskTrendBucket(date=d, **buckets[d]) for d in sorted(buckets)],
    )
