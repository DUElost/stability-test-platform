"""precheck_reaper — Reconcile stale precheck PlanRun rows.

Runs as an APScheduler periodic job.  Scans for ``status='RUNNING'`` PlanRun
rows that have **no** child ``JobInstance`` rows (meaning the dispatch gate
never materialised jobs), inspects their ``run_context.dispatch_state`` and
the associated SAQ job status, and either:

- **re-enqueues** the precheck task when the SAQ job is missing entirely
  and the re-enqueue cap has not been reached, or
- **fails** the PlanRun when the SAQ job was aborted/swept, or the worker
  that owns it is no longer alive while the job appears stale.

The reaper is a pure sync function (APScheduler thread-pool job) and uses
the sync SAQ helpers exposed by ``backend.tasks.saq_worker`` to peek at
queue state without deadlocking the main event loop.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.core.database import SessionLocal
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun

# Import sync SAQ helpers at module level so tests can patch them.
# The underlying functions safely return None when the queue is not initialised.
from backend.tasks.saq_worker import (
    EnqueueSyncError,
    get_saq_job_state_sync,
    is_worker_alive_sync,
    enqueue_sync,
)

logger = logging.getLogger(__name__)

PRECHECK_QUEUE_STALE_SECONDS = int(os.getenv("PRECHECK_QUEUE_STALE_SECONDS", "90"))
PRECHECK_ACTIVE_STALE_SECONDS = int(os.getenv("PRECHECK_ACTIVE_STALE_SECONDS", "180"))
MAX_PRECHECK_REENQUEUE_ATTEMPTS = int(os.getenv("MAX_PRECHECK_REENQUEUE_ATTEMPTS", "1"))


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_stale_iso(iso_ts: str | None, stale_seconds: int) -> bool:
    """Return True if *iso_ts* is more than *stale_seconds* in the past."""
    if not iso_ts:
        return False
    try:
        if iso_ts.endswith("Z"):
            ts = datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                tzinfo=timezone.utc
            )
        elif "+" in iso_ts or iso_ts.count("-") > 2:
            ts = datetime.fromisoformat(iso_ts)
        else:
            ts = datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%S.%f").replace(
                tzinfo=timezone.utc
            )
        return (_utc_now() - ts).total_seconds() > stale_seconds
    except (ValueError, TypeError):
        logger.debug("_is_stale_iso parse failure iso=%r", iso_ts)
        return False


def _is_stale_epoch_ms(epoch_ms: int | float | None, stale_seconds: int) -> bool:
    """Return True if *epoch_ms* is more than *stale_seconds* ago."""
    if epoch_ms is None:
        return False
    try:
        return (time.time() - float(epoch_ms) / 1000.0) > stale_seconds
    except (TypeError, ValueError):
        return False


def _fail_plan_run(pr: PlanRun, db: Session, reason: str) -> None:
    """Transition a RUNNING PlanRun to FAILED with a clear reason."""
    pr.status = "FAILED"
    pr.ended_at = _utc_now()
    run_ctx = dict(pr.run_context or {})
    dispatch_state = dict(run_ctx.get("dispatch_state") or {})
    dispatch_state["status"] = "failed"
    dispatch_state["last_error"] = reason
    dispatch_state["completed_at"] = _utc_iso()
    run_ctx["dispatch_state"] = dispatch_state

    summary = dict(pr.result_summary or {})
    summary["precheck_failed"] = True
    summary["reason"] = reason
    pr.result_summary = summary
    pr.run_context = run_ctx
    flag_modified(pr, "run_context")
    db.commit()
    logger.warning("precheck_reaper_failed plan_run=%d reason=%s", pr.id, reason)


def _patch_dispatch_state(pr: PlanRun, db: Session, **patch: Any) -> None:
    """Update dispatch_state fields without touching other run_context keys."""
    run_ctx = dict(pr.run_context or {})
    dispatch_state = dict(run_ctx.get("dispatch_state") or {})
    dispatch_state.update(patch)
    run_ctx["dispatch_state"] = dispatch_state
    pr.run_context = run_ctx
    flag_modified(pr, "run_context")
    db.commit()


def reconcile_stale_precheck_runs(db: Session | None = None) -> dict[str, int]:
    """Scan for orphan precheck PlanRun rows and take corrective action.

    Can be called from tests with an explicit ``db=`` session, or from the
    scheduler with no arguments (opens its own session).
    """
    own_session = db is None
    if own_session:
        db = SessionLocal()

    try:
        summary: dict[str, int] = {
            "checked": 0,
            "reenqueued": 0,
            "failed": 0,
            "skipped": 0,
        }

        runs = (
            db.query(PlanRun)
            .filter(PlanRun.status == "RUNNING")
            .all()
        )

        for pr in runs:
            # Only inspect runs that have no materialised jobs yet.
            has_jobs = db.query(JobInstance.id).filter(
                JobInstance.plan_run_id == pr.id
            ).first()
            if has_jobs:
                continue

            state = dict((pr.run_context or {}).get("dispatch_state") or {})
            if not state or not state.get("enqueue_key"):
                continue

            # Avoid touching runs that already reached a terminal dispatch_state.
            if state.get("status") in ("completed", "failed"):
                summary["skipped"] += 1
                continue

            job = get_saq_job_state_sync(state["enqueue_key"])
            summary["checked"] += 1

            # ── Case 1: SAQ job was aborted / swept ──────────────────────
            if job and job.get("status") == "aborted":
                _fail_plan_run(
                    pr, db,
                    f"precheck_job_aborted:{job.get('error') or 'unknown'}",
                )
                summary["failed"] += 1
                continue

            # ── Case 2: SAQ job is active but the owning worker is gone ──
            if (
                job
                and job.get("status") == "active"
                and _is_stale_epoch_ms(
                    job.get("started"), PRECHECK_ACTIVE_STALE_SECONDS
                )
                and not is_worker_alive_sync(job.get("worker_id"))
            ):
                _fail_plan_run(pr, db, "precheck_worker_lost")
                summary["failed"] += 1
                continue

            # ── Case 3: SAQ job missing entirely — re-enqueue once ───────
            if (
                not job
                and state.get("started_at") is None
                and int(state.get("requeue_attempts") or 0)
                < MAX_PRECHECK_REENQUEUE_ATTEMPTS
                and _is_stale_iso(
                    state.get("enqueued_at"), PRECHECK_QUEUE_STALE_SECONDS
                )
            ):
                try:
                    enqueue_sync(
                        "precheck_and_dispatch_task",
                        key=state["enqueue_key"],
                        timeout=600,
                        retries=0,
                        required=True,
                        plan_run_id=pr.id,
                    )
                except EnqueueSyncError as exc:
                    logger.warning(
                        "precheck_reaper_reenqueue_failed plan_run=%d err=%s",
                        pr.id,
                        exc,
                    )
                    _patch_dispatch_state(
                        pr,
                        db,
                        last_error=f"precheck_reenqueue_failed:{exc}",
                    )
                    summary["skipped"] += 1
                    continue
                _patch_dispatch_state(
                    pr,
                    db,
                    requeue_attempts=int(state.get("requeue_attempts") or 0) + 1,
                    status="queued",
                    last_error="precheck_job_missing_reenqueued",
                )
                summary["reenqueued"] += 1
                continue

            summary["skipped"] += 1

        if summary["checked"]:
            logger.info(
                "precheck_reaper_done checked=%d reenqueued=%d failed=%d skipped=%d",
                summary["checked"],
                summary["reenqueued"],
                summary["failed"],
                summary["skipped"],
            )

    finally:
        if own_session:
            db.close()

    return summary


def precheck_reaper_job() -> None:
    """APScheduler-compatible callback (sync, runs in thread pool)."""
    reconcile_stale_precheck_runs()
