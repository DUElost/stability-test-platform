"""Post-dispatch dispatch_state sync after complete_plan_run_dispatch."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from backend.models.plan_run import PlanRun

from . import utc_iso
from .state import update_dispatch_state

logger = logging.getLogger(__name__)


def dispatch_complete(
    pr: PlanRun,
    db: Session,
    *,
    out_of_sync_hosts: list[str],
) -> str:
    """Align dispatch_state with PlanRun status after job materialisation.

    Returns Prometheus gate_outcome: ``failed`` | ``passed`` | ``synced_passed``.
    """
    if pr.status == "FAILED":
        result_summary = pr.result_summary or {}
        missing = result_summary.get("missing_scripts") or []
        last_error = (
            f"dispatch_failed: {','.join(missing)}"
            if missing
            else "dispatch_failed"
        )
        update_dispatch_state(
            pr,
            db,
            status="failed",
            completed_at=utc_iso(),
            last_error=last_error,
        )
        logger.info(
            "precheck_dispatch_failed plan_run=%d missing=%s",
            pr.id,
            missing,
        )
        return "failed"

    logger.info("precheck_dispatched plan_run=%d", pr.id)
    update_dispatch_state(
        pr,
        db,
        status="completed",
        completed_at=utc_iso(),
        last_error=None,
    )
    return "synced_passed" if out_of_sync_hosts else "passed"
