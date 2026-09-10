# -*- coding: utf-8 -*-
"""
Post-completion Pipeline — runs after a JobInstance reaches a terminal state.

Generates the run report + JIRA draft, persists them into JobInstance columns
(report_json, jira_draft_json, post_processed_at) so they can be served
instantly by the API without recomputation.
"""

import logging
import os
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
            from backend.services.plan_run_aggregation import maybe_notify_risk_high

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


def refresh_report_cache_for_plan_run(plan_run_id: int) -> int:
    """#1082：PlanRun 终态后批量重算 job.report_json —— 快照变为最终结果。

    裁决语义（见 Agent Note 2026-09-09-report-cache-snapshot-1082）：
      - 单 Job 完成时缓存「当时整个 PlanRun 的报告快照」（post_completion 原行为）；
      - PlanRun 终态（_finalize_plan_run）时数据才真正静止，此时统一刷新一次，
        此后「快照 = 最新最终结果」，两个口径收敛。

    只刷 post_processed_at 非空的 job（曾生成过报告的）；重算失败逐条跳过不影响
    其他 job。独立 SessionLocal：调用方（线程池 / 聚合事务外）不需要持有会话。
    """
    from backend.core.database import SessionLocal
    from backend.services.report_service import compose_run_report

    refreshed = 0
    with SessionLocal() as db:
        jobs = (
            db.query(JobInstance)
            .filter(
                JobInstance.plan_run_id == plan_run_id,
                JobInstance.post_processed_at.isnot(None),
            )
            .all()
        )
        for job in jobs:
            try:
                report = compose_run_report(db, job.id)
            except Exception:
                logger.exception(
                    "report_cache_refresh_failed job_id=%d", job.id,
                )
                continue
            if report is None:
                continue
            job.report_json = report.model_dump(mode="json")
            # post_processed_at 语义 = 报告生成时刻：终态刷新后即「最终结果」
            # 的生成时刻，/report/cached 的 cached_at 标注据此展示。
            job.post_processed_at = datetime.now(timezone.utc)
            refreshed += 1
        db.commit()
    if refreshed:
        logger.info(
            "report_cache_refreshed plan_run=%d count=%d", plan_run_id, refreshed,
        )
    return refreshed


def _schedule_report_cache_refresh(plan_run_id: int) -> None:
    """#1082：终态后调度批量刷新报告缓存（best-effort，绝不外溢）。

    TESTING=1（pytest 环境）跳过：后台线程与「按用例 TRUNCATE」的数据库隔离
    模型天然竞争，会污染无关用例；需要验证刷新逻辑的用例直接调用
    ``refresh_report_cache_for_plan_run``。
    """
    if os.getenv("TESTING") == "1":
        return
    try:
        from backend.core.thread_pool import submit as _pool_submit

        _pool_submit(refresh_report_cache_for_plan_run, int(plan_run_id))
    except Exception:
        logger.debug(
            "report_cache_refresh_scheduling_failed plan_run=%s", plan_run_id,
        )
