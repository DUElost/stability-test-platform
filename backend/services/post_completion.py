# -*- coding: utf-8 -*-
"""
Post-completion Pipeline — runs after a JobInstance reaches a terminal state.

Generates the run report + JIRA draft, persists them into JobInstance columns
(report_json, jira_draft_json, post_processed_at) so they can be served
instantly by the API without recomputation.

#3299 边界：本模块只负责**单 Job** 的后处理。Run 级副作用（链式恢复、RISK_HIGH、
终态报告缓存刷新）一律经 ``backend.services.plan_run_finalization`` 编排者调用，
不得直接 import ``plan_chain_trigger`` / ``plan_run_aggregation``——那两条函数体内
的 import 曾是五模块环的闭合边。
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.models.job import JobInstance

logger = logging.getLogger(__name__)


def _retry_stuck_case_ingest(job_id: int, db: Session) -> bool:
    """Repair jobs marked post-processed while case ingest was still pending."""
    from backend.services.case_result_ingest import (
        case_result_ingest_pending,
        ingest_test_case_results_for_job,
    )

    if not case_result_ingest_pending(db, job_id):
        return True
    logger.info(
        "post_completion: job %d processed but case ingest pending, retrying",
        job_id,
    )
    ingest_test_case_results_for_job(db, job_id)
    db.commit()
    if case_result_ingest_pending(db, job_id):
        logger.warning(
            "post_completion: job %d case ingest still pending after retry",
            job_id,
        )
        return False
    return True


def run_post_completion(job_id: int, db: Session) -> bool:
    """Synchronous post-completion for a single JobInstance.

    Returns True if the report was successfully generated and persisted.
    """
    from backend.services.report_service import (
        build_jira_draft,
        compose_run_report,
    )

    job = db.get(JobInstance, job_id)
    if not job:
        logger.warning("post_completion: job %d not found, skipping", job_id)
        return False

    # Durable chain repair: this task is retried independently from the
    # terminal request and can recover a process crash between the parent CAS
    # and child PlanRun creation. #3299：经编排者的恢复入口，不再直接 import
    # plan_chain_trigger（环边）。
    try:
        from backend.services.plan_run_finalization import recover_chain_trigger
        recover_chain_trigger(job.plan_run_id, db)
    except Exception:
        db.rollback()
        logger.exception(
            "post_completion: chain reconciliation failed for job %d", job_id,
        )

    if job.post_processed_at is not None:
        logger.debug(
            "post_completion: job %d already processed at %s",
            job_id,
            job.post_processed_at,
        )
        return _retry_stuck_case_ingest(job_id, db)

    try:
        from backend.services.case_result_ingest import (
            case_result_ingest_pending,
            ingest_test_case_results_for_job,
        )

        report = compose_run_report(db, job_id)
        if report is None:
            logger.warning(
                "post_completion: compose_run_report returned None for job %d",
                job_id,
            )
            return False

        report_dict = report.model_dump(mode="json")
        job.report_json = report_dict

        try:
            jira_draft = build_jira_draft(report)
            job.jira_draft_json = jira_draft.model_dump(mode="json")
        except Exception:
            logger.exception(
                "post_completion: jira draft generation failed for job %d", job_id,
            )

        ingest_test_case_results_for_job(db, job_id)
        if case_result_ingest_pending(db, job_id):
            db.rollback()
            logger.warning(
                "post_completion: job %d case ingest pending, deferring completion",
                job_id,
            )
            return False

        job.post_processed_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("post_completion: job %d report persisted", job_id)

        # RISK_HIGH only when AEE/ANR aggregation reaches S (once per PlanRun).
        try:
            from backend.services.plan_run_finalization import maybe_notify_risk_high

            risk_summary = report_dict.get("risk_summary")
            if not isinstance(risk_summary, dict) and hasattr(report, "risk_summary"):
                risk_summary = report.risk_summary
            maybe_notify_risk_high(
                db,
                plan_run_id=job.plan_run_id,
                risk_summary=risk_summary if isinstance(risk_summary, dict) else None,
            )
        except Exception:
            logger.exception(
                "post_completion: risk_high notification failed for job %d", job_id,
            )

        return True

    except Exception:
        logger.exception("post_completion: failed for job %d", job_id)
        db.rollback()
        return False


def run_post_completion_async(job_id: int) -> None:
    """Fire-and-forget wrapper that opens its own DB session."""
    from backend.core.database import SessionLocal

    db = SessionLocal()
    try:
        run_post_completion(job_id, db)
    finally:
        db.close()


# refresh_report_cache_for_plan_run / _schedule_report_cache_refresh 自 #3299 起
# 住在 backend.services.plan_run_finalization.py（终态副作用编排者）。
