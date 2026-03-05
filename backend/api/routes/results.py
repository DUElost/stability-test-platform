# -*- coding: utf-8 -*-
"""
Results summary API — aggregated test run statistics for the dashboard.
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.models.job import JobInstance, StepTrace, TaskTemplate
from backend.models.workflow import WorkflowDefinition, WorkflowRun

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
    duration_seconds: Optional[float] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class ResultsSummary(BaseModel):
    runs_by_status: RunsByStatus
    test_type_stats: List[TestTypeStat]
    risk_distribution: RiskDistribution
    recent_runs: List[RecentRun]


# ---------- Helpers ----------

_JOB_STATUS_TO_RUN_STATUS = {
    "PENDING": "QUEUED",
    "PENDING_TOOL": "QUEUED",
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
    db: Session = Depends(get_db),
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
                "task_template",
                "workflow_run",
                "workflow_definition",
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
        # --- runs_by_status (新链路：JobInstance) ---
        status_counts = (
            db.query(JobInstance.status, func.count(JobInstance.id))
            .group_by(JobInstance.status)
            .all()
        )
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

        # --- test_type_stats (按 TaskTemplate.name 聚合) ---
        type_rows = (
            db.query(
                TaskTemplate.name,
                JobInstance.status,
                func.count(JobInstance.id),
            )
            .join(TaskTemplate, JobInstance.task_template_id == TaskTemplate.id)
            .group_by(TaskTemplate.name, JobInstance.status)
            .all()
        )
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

        # --- recent_runs ---
        recent_rows = (
            db.query(JobInstance, WorkflowDefinition.name, TaskTemplate.name)
            .join(WorkflowRun, JobInstance.workflow_run_id == WorkflowRun.id)
            .join(WorkflowDefinition, WorkflowRun.workflow_definition_id == WorkflowDefinition.id)
            .join(TaskTemplate, JobInstance.task_template_id == TaskTemplate.id)
            .order_by(JobInstance.id.desc())
            .limit(limit)
            .all()
        )
        recent_job_ids = [job.id for job, _wf_name, _template_name in recent_rows]
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
        for job, wf_name, template_name in recent_rows:
            log_summary = _extract_log_summary_from_snapshot(snapshot_map.get(job.id))
            risk = _parse_risk_level(log_summary)
            duration = None
            if job.started_at and job.ended_at:
                duration = (job.ended_at - job.started_at).total_seconds()
            workflow_name = str(wf_name or "workflow")
            template_name_norm = str(template_name or "default")
            recent_runs.append(
                RecentRun(
                    run_id=job.id,
                    task_name=f"{workflow_name}/{template_name_norm}",
                    task_type=template_name_norm,
                    status=_normalize_job_status(job.status),
                    risk_level=risk,
                    duration_seconds=duration,
                    started_at=job.started_at,
                    finished_at=job.ended_at,
                )
            )

        # --- risk_distribution ---
        total_jobs = int(db.query(func.count(JobInstance.id)).scalar() or 0)
        risk_counts = {"high": 0, "medium": 0, "low": 0, "unknown": 0}
        if total_jobs > 0:
            all_snapshot_rows = (
                db.query(StepTrace.job_id, StepTrace.output)
                .filter(
                    StepTrace.step_id == "__job__",
                    StepTrace.event_type == "RUN_COMPLETE",
                )
                .all()
            )
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
