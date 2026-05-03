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

    if job.post_processed_at is not None:
        logger.debug("post_completion: job %d already processed at %s", job_id, job.post_processed_at)
        return True

    try:
        report = compose_run_report(db, job_id)
        if report is None:
            logger.warning("post_completion: compose_run_report returned None for job %d", job_id)
            return False

        report_dict = report.model_dump(mode="json") if hasattr(report, "model_dump") else report.dict()
        job.report_json = report_dict

        try:
            jira_draft = build_jira_draft(report)
            job.jira_draft_json = (
                jira_draft.model_dump(mode="json")
                if hasattr(jira_draft, "model_dump")
                else jira_draft.dict()
            )
        except Exception:
            logger.exception("post_completion: jira draft generation failed for job %d", job_id)

        job.post_processed_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("post_completion: job %d report persisted", job_id)
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
