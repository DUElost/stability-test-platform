"""PlanRun DLE / 用例结果读侧装配（#1520：log-events + test-case-results）。

查询已在 ``device_log_event`` / ``case_result_ingest``；本模块只做 Out 装配。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from backend.api.schemas.case_result import (
    TestCaseResultOut,
    TestCaseResultSummary,
    TestCaseResultsPayload,
)
from backend.api.schemas.plan_run import PlanRunLogEventOut, PlanRunLogEventsOut
from backend.models.job import JobInstance
from backend.services.case_result_ingest import list_plan_run_test_case_results
from backend.services.device_log_event import (
    list_plan_run_device_log_event_platforms,
    list_plan_run_device_log_events,
)
from backend.services.plan_run_read_common import iso


def build_plan_run_log_events(
    db: Session,
    run_id: int,
    *,
    skip: int = 0,
    limit: int = 100,
    state: Optional[str] = None,
    platform: Optional[str] = None,
) -> PlanRunLogEventsOut:
    rows, total = list_plan_run_device_log_events(
        db,
        run_id,
        skip=skip,
        limit=limit,
        state=state,
        platform=platform,
    )
    items = [
        PlanRunLogEventOut(
            id=str(row.id),
            serial=row.serial,
            platform=row.platform,
            event_type=row.event_type,
            event_subtype=row.event_subtype,
            state=row.state,
            local_path=row.local_path,
            remote_path=row.remote_path,
            detected_at=iso(row.detected_at) or "",
            device_timestamp=iso(row.device_timestamp),
            job_id=row.job_id,
            host_id=row.host_id,
            signal_seq_no=row.signal_seq_no,
        )
        for row in rows
    ]
    return PlanRunLogEventsOut(
        plan_run_id=run_id,
        total=total,
        items=items,
        # #2288：平台全集单独取——不受本次 `platform`/`limit` 影响。
        platforms=list_plan_run_device_log_event_platforms(db, run_id, state=state),
    )


def build_plan_run_test_case_results(
    db: Session,
    run_id: int,
    *,
    skip: int = 0,
    limit: int = 500,
    status: Optional[str] = None,
) -> TestCaseResultsPayload:
    rows, total, summary_counts = list_plan_run_test_case_results(
        db, run_id, status=status, skip=skip, limit=limit,
    )
    job_ids = {row.job_id for row in rows}
    jobs_by_id: dict[int, JobInstance] = {}
    if job_ids:
        jobs_by_id = {
            j.id: j
            for j in db.query(JobInstance).filter(JobInstance.id.in_(job_ids)).all()
        }
    items = []
    for row in rows:
        job = jobs_by_id.get(row.job_id)
        items.append(TestCaseResultOut(
            id=row.id,
            plan_run_id=row.plan_run_id,
            job_id=row.job_id,
            suite_id=row.suite_id,
            case_id=row.case_id,
            case_name=row.case_name,
            status=row.status,
            detail=row.detail,
            artifact_uri=row.artifact_uri,
            run_dir=row.run_dir,
            created_at=row.created_at,
            device_id=job.device_id if job else None,
            host_id=str(job.host_id) if job and job.host_id is not None else None,
        ))
    return TestCaseResultsPayload(
        items=items,
        total=total,
        summary=TestCaseResultSummary(**summary_counts),
    )
