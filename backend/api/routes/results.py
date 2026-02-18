# -*- coding: utf-8 -*-
"""
Results summary API — aggregated test run statistics for the dashboard.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.models.schemas import Task, TaskRun, RunStatus

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

    # --- runs_by_status ---
    status_counts = (
        db.query(TaskRun.status, func.count(TaskRun.id))
        .group_by(TaskRun.status)
        .all()
    )
    status_map: Dict[str, int] = {
        (s.value if hasattr(s, 'value') else str(s)): c
        for s, c in status_counts
    }
    runs_by_status = RunsByStatus(
        finished=status_map.get(RunStatus.FINISHED.value, 0),
        failed=status_map.get(RunStatus.FAILED.value, 0),
        canceled=status_map.get(RunStatus.CANCELED.value, 0),
        running=status_map.get(RunStatus.RUNNING.value, 0)
        + status_map.get(RunStatus.DISPATCHED.value, 0),
        total=sum(status_map.values()),
    )

    # --- test_type_stats ---
    type_rows = (
        db.query(
            Task.type,
            TaskRun.status,
            func.count(TaskRun.id),
        )
        .join(Task, TaskRun.task_id == Task.id)
        .group_by(Task.type, TaskRun.status)
        .all()
    )
    type_agg: Dict[str, Dict[str, int]] = {}
    for task_type, run_status, cnt in type_rows:
        bucket = type_agg.setdefault(task_type, {"finished": 0, "failed": 0, "total": 0})
        bucket["total"] += cnt
        status_str = run_status.value if hasattr(run_status, 'value') else str(run_status)
        if status_str == RunStatus.FINISHED.value:
            bucket["finished"] += cnt
        elif status_str == RunStatus.FAILED.value:
            bucket["failed"] += cnt
    test_type_stats = [
        TestTypeStat(type=t, **counts) for t, counts in sorted(type_agg.items())
    ]

    # --- recent_runs (with risk) ---
    recent_rows = (
        db.query(TaskRun, Task.name, Task.type)
        .join(Task, TaskRun.task_id == Task.id)
        .order_by(TaskRun.id.desc())
        .limit(limit)
        .all()
    )
    recent_runs: List[RecentRun] = []
    for run, task_name, task_type in recent_rows:
        risk = _parse_risk_level(run.log_summary)
        duration = None
        if run.started_at and run.finished_at:
            duration = (run.finished_at - run.started_at).total_seconds()
        recent_runs.append(
            RecentRun(
                run_id=run.id,
                task_name=task_name,
                task_type=task_type,
                status=str(run.status.value) if hasattr(run.status, "value") else str(run.status),
                risk_level=risk,
                duration_seconds=duration,
                started_at=run.started_at,
                finished_at=run.finished_at,
            )
        )

    # --- risk_distribution (across ALL runs — single SQL query) ---
    risk_row = db.query(
        func.count(case((TaskRun.log_summary.like('%risk=HIGH%'), 1))).label('high'),
        func.count(case((TaskRun.log_summary.like('%risk=MEDIUM%'), 1))).label('medium'),
        func.count(case((TaskRun.log_summary.like('%risk=LOW%'), 1))).label('low'),
        func.count(TaskRun.id).label('total'),
    ).first()
    risk_counts = {
        "high": risk_row.high or 0,
        "medium": risk_row.medium or 0,
        "low": risk_row.low or 0,
        "unknown": (risk_row.total or 0) - (risk_row.high or 0) - (risk_row.medium or 0) - (risk_row.low or 0),
    }

    return ResultsSummary(
        runs_by_status=runs_by_status,
        test_type_stats=test_type_stats,
        risk_distribution=RiskDistribution(**risk_counts),
        recent_runs=recent_runs,
    )
