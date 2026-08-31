"""ADR-0030 P2 — 从 mtbf_finish NFS JSON 摄入 test_case_result。

触发点：``post_completion``（Job 终态后）。幂等键 = ``(job_id, case_name)``。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from backend.models.job import JobInstance, StepTrace
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.suite import TestCase
from backend.models.case_result import TestCaseResult
from backend.services.report_service import _safe_json_loads

logger = logging.getLogger(__name__)

_VALID_STATUSES = frozenset({"PASS", "FAILURE", "ERROR"})


def _resolve_suite_id(db: Session, job: JobInstance) -> Optional[int]:
    if job.plan_run_id is None:
        return None
    pr = db.get(PlanRun, job.plan_run_id)
    if pr is None:
        return None
    dispatch = (pr.run_context or {}).get("dispatch_suite") or {}
    suite_id = dispatch.get("suite_id")
    if suite_id is not None:
        return int(suite_id)
    plan = db.get(Plan, pr.plan_id) if pr.plan_id else None
    return plan.suite_id if plan else None


def _find_finish_detail_uri(db: Session, job_id: int) -> Optional[str]:
    traces = (
        db.query(StepTrace)
        .filter(StepTrace.job_id == job_id)
        .order_by(StepTrace.id.desc())
        .all()
    )
    for trace in traces:
        payload = _safe_json_loads(trace.output)
        detail_uri = payload.get("detail_uri")
        if isinstance(detail_uri, str) and detail_uri.strip():
            return detail_uri.strip()
    return None


def _load_detail_json(detail_uri: str) -> Optional[dict[str, Any]]:
    path = Path(detail_uri)
    if not path.is_file():
        logger.warning("test_case_result_ingest: detail file missing path=%s", detail_uri)
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("test_case_result_ingest: read failed path=%s err=%s", detail_uri, exc)
        return None
    return payload if isinstance(payload, dict) else None


def _case_detail_message(testpoint: dict[str, Any]) -> Optional[str]:
    for tc in testpoint.get("testcases") or []:
        if not isinstance(tc, dict):
            continue
        status = str(tc.get("status") or "").upper()
        if status in {"FAILURE", "ERROR"}:
            message = (tc.get("message") or "").strip()
            if message:
                return message[:2000]
    return None


def _match_case_id(db: Session, suite_id: Optional[int], case_name: str) -> Optional[int]:
    if suite_id is None or not case_name:
        return None
    row = (
        db.query(TestCase.id)
        .filter(TestCase.suite_id == suite_id, TestCase.name == case_name)
        .first()
    )
    return int(row[0]) if row else None


def ingest_test_case_results_for_job(db: Session, job_id: int) -> int:
    """从 mtbf_finish detail JSON 摄入逐条结果。返回新写入行数（已存在则跳过）。"""
    job = db.get(JobInstance, job_id)
    if job is None or job.plan_run_id is None:
        return 0

    existing = (
        db.query(TestCaseResult.id)
        .filter(TestCaseResult.job_id == job_id)
        .first()
    )
    if existing is not None:
        return 0

    detail_uri = _find_finish_detail_uri(db, job_id)
    if not detail_uri:
        return 0

    payload = _load_detail_json(detail_uri)
    if not payload:
        return 0

    testpoints = payload.get("testpoints")
    if not isinstance(testpoints, list) or not testpoints:
        return 0

    suite_id = _resolve_suite_id(db, job)
    run_dir = payload.get("run_dir")
    if isinstance(run_dir, str):
        run_dir = run_dir.strip() or None
    else:
        metrics = payload.get("metrics")
        if isinstance(metrics, dict) and metrics.get("run_dir"):
            run_dir = str(metrics["run_dir"])

    inserted = 0
    for tp in testpoints:
        if not isinstance(tp, dict):
            continue
        case_name = str(tp.get("name") or "").strip()
        if not case_name:
            continue
        status = str(tp.get("status") or "").upper()
        if status not in _VALID_STATUSES:
            status = "ERROR"
        row = TestCaseResult(
            plan_run_id=job.plan_run_id,
            job_id=job.id,
            suite_id=suite_id,
            case_id=_match_case_id(db, suite_id, case_name),
            case_name=case_name,
            status=status,
            detail=_case_detail_message(tp),
            artifact_uri=detail_uri,
            run_dir=run_dir,
        )
        db.add(row)
        inserted += 1

    if inserted:
        db.flush()
        logger.info(
            "test_case_result_ingest: job=%s plan_run=%s rows=%d uri=%s",
            job_id, job.plan_run_id, inserted, detail_uri,
        )
    return inserted


def list_plan_run_test_case_results(
    db: Session,
    plan_run_id: int,
    *,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 500,
) -> tuple[list[TestCaseResult], int, dict[str, int]]:
    q = db.query(TestCaseResult).filter(TestCaseResult.plan_run_id == plan_run_id)
    if status:
        q = q.filter(TestCaseResult.status == status.upper())
    total = q.count()
    rows = (
        q.order_by(TestCaseResult.job_id.asc(), TestCaseResult.case_name.asc())
        .offset(skip)
        .limit(min(limit, 2000))
        .all()
    )
    summary = {"total": 0, "passed": 0, "failed": 0, "error": 0}
    for (st,) in db.query(TestCaseResult.status).filter(
        TestCaseResult.plan_run_id == plan_run_id,
    ):
        summary["total"] += 1
        norm = (st or "").upper()
        if norm == "PASS":
            summary["passed"] += 1
        elif norm == "FAILURE":
            summary["failed"] += 1
        elif norm == "ERROR":
            summary["error"] += 1
    return rows, total, summary
