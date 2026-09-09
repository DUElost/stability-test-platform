# -*- coding: utf-8 -*-
"""
Post-completion Pipeline — runs after a JobInstance reaches a terminal state.

Generates the run report + JIRA draft, persists them into JobInstance columns
(report_json, jira_draft_json, post_processed_at) so they can be served
instantly by the API without recomputation.
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


def _complete_case_ingest(job: JobInstance, db: Session) -> bool:
    """两段提交第二段：用例摄入 + ``post_processed_at`` 终态标记。

    ``report_json`` 已由调用方先落库（第一段）。#1076 要求 post_processed_at
    晚于用例摄入；detail JSON 晚到/损坏导致摄入暂未就绪时返回 False，报告不受
    影响，recycler 按 ``post_processed_at IS NULL`` 周期重试补摄入。
    """
    from backend.services.case_result_ingest import (
        case_result_ingest_pending,
        ingest_test_case_results_for_job,
    )

    ingest_test_case_results_for_job(db, job.id)
    if case_result_ingest_pending(db, job.id):
        db.rollback()
        logger.warning(
            "post_completion: job %d case ingest pending, deferring completion",
            job.id,
        )
        return False

    job.post_processed_at = datetime.now(timezone.utc)
    db.commit()
    logger.info("post_completion: job %d case ingest finalized", job.id)

    # RISK_HIGH only when AEE/ANR aggregation reaches S (once per PlanRun).
    try:
        from backend.services.plan_run_aggregation import maybe_notify_risk_high

        report_dict = job.report_json
        risk_summary = report_dict.get("risk_summary") if isinstance(report_dict, dict) else None
        maybe_notify_risk_high(
            db,
            plan_run_id=job.plan_run_id,
            risk_summary=risk_summary if isinstance(risk_summary, dict) else None,
        )
    except Exception:
        logger.exception(
            "post_completion: risk_high notification failed for job %d", job.id,
        )
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
    # and child PlanRun creation.
    try:
        from backend.services.plan_chain_trigger import reconcile_chain_trigger_sync
        reconcile_chain_trigger_sync(job.plan_run_id, db)
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

    # 两段提交（#1076 follow-up）：报告先落库，post_processed_at 延后到用例摄入
    # 成功。compose_run_report 不读 test_case_result（数据源为 job 终态快照与
    # artifacts），摄入暂未就绪时不得把已生成的报告一并回滚——否则 detail JSON
    # 永久损坏/合法零用例的 job 将永远没有报告，且每次重试都在空转重算。
    try:
        if job.report_json is None:
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

            db.commit()
            logger.info("post_completion: job %d report persisted", job_id)

        return _complete_case_ingest(job, db)

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
