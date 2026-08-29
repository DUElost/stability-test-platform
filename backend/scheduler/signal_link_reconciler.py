"""#556 — periodic ``job_log_signal`` ↔ ``device_log_event`` link repair.

Signal ingest (``backend/api/routes/agent_api.py``) already links on arrival.
This sweep only drains the backlog left by ordering races — a DLE row landing
before its ``job_log_signal`` row, so the write path had nothing to match yet.

It used to run inside ``GET /plan-runs/{id}/watcher-summary``, but that route's
session comes from ``get_db()``, which never commits: the UPDATE was rolled
back on every request while still taking row locks on a page that polls every
3s/10s/30s. Repair belongs to a writer with its own transaction.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy import text

from backend.core.database import SessionLocal
from backend.services.device_log_event import link_signals_to_device_log_events_sync

logger = logging.getLogger(__name__)

SIGNAL_LINK_RECONCILE_BATCH = int(
    os.getenv("STP_SIGNAL_LINK_RECONCILE_BATCH", "200")
)

# Distinct job_ids that still hold an unlinked signal with a matchable DLE.
# Newest-first + LIMIT keeps each tick bounded; the backlog drains over
# successive ticks instead of one unbounded UPDATE.
_CANDIDATE_JOB_IDS_SQL = text(
    """
    SELECT DISTINCT s.job_id
      FROM job_log_signal AS s
      JOIN device_log_event AS e
        ON e.job_id = s.job_id
       AND e.signal_seq_no = s.seq_no
     WHERE s.device_log_event_id IS NULL
       AND e.signal_seq_no IS NOT NULL
     ORDER BY s.job_id DESC
     LIMIT :limit
    """
)


def reconcile_signal_links_once(
    *,
    batch_size: int | None = None,
) -> dict[str, Any]:
    """Link backlog signals to their DLE. Leader-guarded (ADR-0027 P3-1)."""
    from backend.core.leader_election import hold_scheduler_leadership

    with hold_scheduler_leadership("signal_link_reconcile") as is_leader:
        if not is_leader:
            return {"scanned": 0, "linked": 0, "skipped_not_leader": 1}
        return _reconcile_signal_links_body(batch_size=batch_size)


def _reconcile_signal_links_body(
    *,
    batch_size: int | None = None,
) -> dict[str, Any]:
    limit = SIGNAL_LINK_RECONCILE_BATCH if batch_size is None else batch_size
    summary: dict[str, Any] = {"scanned": 0, "linked": 0}

    with SessionLocal() as db:
        job_ids = [
            int(row[0])
            for row in db.execute(_CANDIDATE_JOB_IDS_SQL, {"limit": limit})
        ]
        summary["scanned"] = len(job_ids)
        if not job_ids:
            db.rollback()
            return summary

        try:
            summary["linked"] = link_signals_to_device_log_events_sync(db, job_ids)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "signal_link_reconcile_failed jobs=%d", len(job_ids),
            )
            raise

    if summary["linked"]:
        logger.info("signal_link_reconcile_done %s", summary)
    else:
        logger.debug("signal_link_reconcile_done %s", summary)
    return summary
