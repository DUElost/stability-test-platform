# -*- coding: utf-8 -*-
"""
Post-completion Pipeline — FROZEN

!! DO NOT CALL — this module operates on legacy TaskRun which lacks the
required columns (report_json, jira_draft_json, post_processed_at) on
the new JobInstance model.  It will be rewritten after the dual-track ORM
merge adds those columns to job_instance.  See ADR-0008 and ADR-0012.

Frozen on: 2026-03-26
"""

import logging

logger = logging.getLogger(__name__)

_FROZEN_MSG = (
    "post_completion is FROZEN: TaskRun-based path cannot run against "
    "JobInstance. Complete the dual-track ORM merge (ADR-0008) first."
)


def run_post_completion(run_id: int) -> None:
    logger.warning(_FROZEN_MSG, extra={"run_id": run_id})


def run_post_completion_async(run_id: int) -> None:
    logger.warning(_FROZEN_MSG, extra={"run_id": run_id})
