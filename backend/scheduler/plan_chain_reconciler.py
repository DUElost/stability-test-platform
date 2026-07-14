"""Durable compensation for interrupted PlanRun chain triggers."""

from __future__ import annotations

import logging
import os

from sqlalchemy import or_, select
from sqlalchemy.orm import aliased

from backend.core.database import SessionLocal
from backend.models.plan_run import PlanRun
from backend.services.plan_chain_trigger import (
    TRIGGERABLE_TERMINAL_STATUSES,
    reconcile_chain_trigger_sync,
)


logger = logging.getLogger(__name__)
CHAIN_RECONCILE_BATCH_SIZE = int(
    os.getenv("CHAIN_RECONCILE_BATCH_SIZE", "100")
)


def reconcile_plan_chains() -> int:
    """Repair eligible terminal parents whose child trigger was interrupted."""
    repaired = 0
    with SessionLocal() as db:
        child = aliased(PlanRun)
        child_exists = (
            select(child.id)
            .where(child.parent_plan_run_id == PlanRun.id)
            .exists()
        )
        parent_ids = list(
            db.execute(
                select(PlanRun.id)
                .where(PlanRun.status.in_(TRIGGERABLE_TERMINAL_STATUSES))
                .where(
                    PlanRun.plan_snapshot["plan"]["next_plan_id"]
                    .as_integer()
                    .is_not(None)
                )
                .where(
                    or_(
                        PlanRun.next_plan_triggered.is_(False),
                        ~child_exists,
                    )
                )
                .order_by(PlanRun.id)
                .limit(CHAIN_RECONCILE_BATCH_SIZE)
            ).scalars()
        )
        for parent_id in parent_ids:
            try:
                child = reconcile_chain_trigger_sync(parent_id, db)
                if child is not None:
                    repaired += 1
            except Exception:
                db.rollback()
                logger.exception(
                    "plan_chain_reconcile_failed parent_plan_run=%d",
                    parent_id,
                )
    if repaired:
        logger.info("plan_chain_reconciled count=%d", repaired)
    return repaired
