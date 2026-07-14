"""Dispatch gate orchestration — verify, sync, and materialise jobs."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.core.audit import record_audit
from backend.core.database import SessionLocal
from backend.core.metrics import record_dispatch_gate
from backend.models.enums import PlanRunStatus
from backend.models.plan_run import PlanRun
from backend.services import precheck as precheck_config
from backend.services.precheck import sync as precheck_sync
from backend.services.precheck import verify as precheck_verify
from backend.services.plan_dispatcher_core import (
    snapshot_dispatch_host_watcher_admin_states,
)
from backend.services.plan_dispatcher_sync import (
    complete_plan_run_dispatch,
    initial_dispatch_state,
)

from . import (
    MIXED_WATCHER_ACTIVITY_CODE,
    MIXED_WATCHER_ACTIVITY_MESSAGE,
    utc_iso,
)
from .dispatch_complete import dispatch_complete
from .idempotency import persist_dispatch_idempotency
from .notify import emit_dispatch_gate_invalidation
from .scripts import expected_scripts_for_run
from .state import (
    initial_precheck_state,
    initialise_precheck_state,
    mark_precheck_failed,
    persist_precheck,
    update_dispatch_state,
)
from .watcher import find_mixed_watcher_inactive_host_ids
from backend.services.state_machine import PlanRunStateMachine

logger = logging.getLogger(__name__)


async def drive_dispatch_gate(
    plan_run_id: int, *, db: Optional[Session] = None
) -> None:
    """Main async coroutine for the ADR-0021 dispatch gate."""
    own_session = db is None
    if own_session:
        db = SessionLocal()
    gate_started_at = time.monotonic()
    gate_outcome = "failed"
    try:
        pr = db.get(PlanRun, plan_run_id)
        if pr is None:
            logger.warning("precheck_no_such_plan_run plan_run=%d", plan_run_id)
            gate_outcome = "skipped"
            return
        if pr.status != "RUNNING":
            logger.info(
                "precheck_skip_non_running plan_run=%d status=%s",
                plan_run_id, pr.status,
            )
            gate_outcome = "skipped"
            return

        update_dispatch_state(
            pr, db,
            status="running",
            started_at=utc_iso(),
            last_error=None,
        )

        precheck = initialise_precheck_state(plan_run_id, db)
        db.refresh(pr)
        persist_dispatch_idempotency(pr, db)
        host_ids = list(precheck["hosts"].keys())
        inactive_host_ids = find_mixed_watcher_inactive_host_ids(pr, host_ids, db)
        if inactive_host_ids:
            for host_id in inactive_host_ids:
                host_state = precheck["hosts"].get(host_id)
                if not host_state:
                    continue
                host_state["status"] = "failed"
                host_state["error"] = "watcher_inactive"
            mark_precheck_failed(
                plan_run_id,
                precheck,
                db,
                error=MIXED_WATCHER_ACTIVITY_MESSAGE,
                code=MIXED_WATCHER_ACTIVITY_CODE,
                inactive_host_ids=inactive_host_ids,
            )
            return

        expected_scripts = expected_scripts_for_run(pr, db)
        if not expected_scripts:
            mark_precheck_failed(
                plan_run_id, precheck, db,
                error="no_scripts_resolved_for_plan_snapshot",
            )
            return

        precheck["phase"] = "verifying"
        persist_precheck(plan_run_id, precheck, db)
        verify_results = await precheck_verify._gather_verify(host_ids, expected_scripts)

        out_of_sync_hosts: list[str] = []
        for hid in host_ids:
            ok, results, err = verify_results[hid]
            host_state = precheck["hosts"][hid]
            host_state["scripts"] = results
            host_state["checked_at"] = utc_iso()
            if ok:
                host_state["status"] = "ok"
            else:
                host_state["status"] = "failed" if err == "agent_offline" else "syncing"
                host_state["error"] = err
                if err != "agent_offline":
                    out_of_sync_hosts.append(hid)
        persist_precheck(plan_run_id, precheck, db)

        offline_hosts = [
            hid for hid in host_ids
            if precheck["hosts"][hid]["error"] == "agent_offline"
        ]
        if offline_hosts:
            mark_precheck_failed(
                plan_run_id, precheck, db,
                error=f"agent_offline: {','.join(offline_hosts)}",
            )
            return

        hosts_to_sync = list(out_of_sync_hosts)
        while hosts_to_sync:
            precheck["phase"] = "syncing"
            persist_precheck(plan_run_id, precheck, db)

            sync_failures: list[tuple[str, str]] = []
            for hid in hosts_to_sync:
                host_state = precheck["hosts"][hid]
                host_state["sync_attempts"] += 1
                host_state["status"] = "syncing"
                host_state["error"] = None

                scripts = host_state.get("scripts") or []
                mismatched_names = {s["name"] for s in scripts if not s.get("ok")}
                mismatched_entries = [
                    es for es in expected_scripts
                    if es["name"] in mismatched_names
                ]

                if not mismatched_entries:
                    sync_failures.append((hid, "no_mismatched_entries"))
                    host_state["status"] = "failed"
                    host_state["error"] = "no_mismatched_entries"
                    continue

                ok_sync, err_sync = await asyncio.to_thread(
                    precheck_sync._push_mismatched_scripts, hid, mismatched_entries, db
                )
                if ok_sync:
                    host_state["synced_at"] = utc_iso()
                    host_state["status"] = "synced"
                    host_state["error"] = None
                else:
                    logger.warning(
                        "lightweight_sync_failed host=%s error=%s — falling back to hot-update",
                        hid, err_sync,
                    )
                    ok_hot, err_hot = await asyncio.to_thread(
                        precheck_sync._sync_host_via_hot_update, hid, db
                    )
                    if ok_hot:
                        host_state["synced_at"] = utc_iso()
                        host_state["status"] = "synced"
                        host_state["error"] = None
                    else:
                        sync_failures.append((hid, err_hot or "hot_update_failed"))
                        host_state["status"] = "failed"
                        host_state["error"] = err_hot or "hot_update_failed"

                persist_precheck(plan_run_id, precheck, db)

            if sync_failures:
                retry_hosts = [
                    h for h, _ in sync_failures
                    if precheck["hosts"][h]["sync_attempts"] < precheck_config.DISPATCH_SYNC_MAX_ATTEMPTS
                ]
                if retry_hosts:
                    logger.info(
                        "precheck_sync_retry plan_run=%d hosts=%s",
                        plan_run_id, retry_hosts,
                    )
                    hosts_to_sync = retry_hosts
                    continue
                msg = "; ".join(f"{h}:{e}" for h, e in sync_failures)
                mark_precheck_failed(
                    plan_run_id, precheck, db, error=f"sync_failed: {msg}",
                )
                return

            precheck["phase"] = "reverifying"
            persist_precheck(plan_run_id, precheck, db)
            await asyncio.sleep(precheck_config.SYNC_SETTLE_SECONDS)

            reverify_results = await precheck_verify._gather_verify(
                hosts_to_sync, expected_scripts,
            )
            still_bad: list[str] = []
            for hid in hosts_to_sync:
                ok, results, err = reverify_results[hid]
                host_state = precheck["hosts"][hid]
                host_state["scripts"] = results
                host_state["checked_at"] = utc_iso()
                if ok:
                    host_state["status"] = "ok"
                    host_state["error"] = None
                else:
                    host_state["status"] = "failed"
                    host_state["error"] = err or "still_mismatch"
                    still_bad.append(hid)
            persist_precheck(plan_run_id, precheck, db)

            if still_bad:
                retry_hosts = [
                    h for h in still_bad
                    if precheck["hosts"][h]["sync_attempts"] < precheck_config.DISPATCH_SYNC_MAX_ATTEMPTS
                ]
                if retry_hosts:
                    logger.info(
                        "precheck_reverify_retry plan_run=%d hosts=%s",
                        plan_run_id, retry_hosts,
                    )
                    hosts_to_sync = retry_hosts
                    continue
                mark_precheck_failed(
                    plan_run_id, precheck, db,
                    error=f"reverify_failed: {','.join(still_bad)}",
                )
                return
            break

        precheck["phase"] = "ready"
        precheck["final_result"] = "ready"
        precheck["completed_at"] = utc_iso()
        persist_precheck(plan_run_id, precheck, db)

        db.refresh(pr)
        persist_dispatch_idempotency(pr, db)

        await asyncio.to_thread(
            complete_plan_run_dispatch, plan_run_id, db
        )

        db.expire(pr)
        pr = db.get(PlanRun, plan_run_id)
        if pr is not None:
            gate_outcome = dispatch_complete(
                pr,
                db,
                out_of_sync_hosts=out_of_sync_hosts,
            )
            if gate_outcome == "failed":
                return

    except Exception:
        logger.exception("precheck_unexpected_failure plan_run=%d", plan_run_id)
        try:
            pr = db.get(PlanRun, plan_run_id)
            if pr is not None and pr.status == "RUNNING":
                run_ctx = dict(pr.run_context or {})
                precheck = run_ctx.get("precheck") or initial_precheck_state([])
                precheck["phase"] = "failed"
                precheck["final_result"] = "failed"
                precheck["completed_at"] = utc_iso()
                precheck.setdefault("errors", []).append("unexpected_exception")
                run_ctx["precheck"] = precheck
                pr.run_context = run_ctx
                PlanRunStateMachine.transition(pr, PlanRunStatus.FAILED, reason="unexpected_exception")
                pr.ended_at = datetime.now(timezone.utc)
                pr.result_summary = {
                    "precheck_failed": True,
                    "reason": "unexpected_exception",
                }
                flag_modified(pr, "run_context")
                db.commit()
                emit_dispatch_gate_invalidation(
                    plan_run_id,
                    phase="failed",
                    dispatch_status="failed",
                )
        except Exception:
            logger.exception("precheck_failure_persist_error plan_run=%d", plan_run_id)
        raise
    finally:
        record_dispatch_gate(gate_outcome, time.monotonic() - gate_started_at)
        if own_session:
            db.close()


_drive_dispatch_gate = drive_dispatch_gate


class PlanRunDispatchRetryError(Exception):
    """Raised when manual precheck/dispatch retry is not allowed."""


def retry_plan_run_dispatch(
    run_id: int,
    db: Session,
    *,
    triggered_by: str,
) -> dict:
    """Reset a failed precheck PlanRun and re-enqueue the dispatch gate."""
    from backend.models.job import JobInstance
    from backend.tasks.saq_worker import EnqueueSyncError, enqueue_sync

    pr = db.get(PlanRun, run_id)
    if pr is None:
        raise PlanRunDispatchRetryError("plan run not found")

    has_jobs = (
        db.query(JobInstance.id)
        .filter(JobInstance.plan_run_id == run_id)
        .first()
    )
    if has_jobs:
        raise PlanRunDispatchRetryError(
            "plan run already has jobs; cannot retry dispatch"
        )

    run_ctx = dict(pr.run_context or {})
    if not run_ctx.get("dispatch_device_ids"):
        raise PlanRunDispatchRetryError("missing dispatch_device_ids")

    summary = dict(pr.result_summary or {})
    precheck = run_ctx.get("precheck") or {}
    dispatch_state = run_ctx.get("dispatch_state") or {}
    was_failed = pr.status == "FAILED" and (
        summary.get("precheck_failed") or summary.get("dispatch_failed")
    )
    eligible = was_failed or (
        pr.status == "RUNNING"
        and (
            precheck.get("phase") == "failed"
            or dispatch_state.get("status") == "failed"
        )
    )
    if not eligible:
        raise PlanRunDispatchRetryError(
            f"plan run not eligible for dispatch retry (status={pr.status})"
        )

    if was_failed:
        PlanRunStateMachine.transition(pr, PlanRunStatus.RUNNING, reason="dispatch_retry")
    # else: pr.status is already RUNNING (precheck phase failed but the
    # top-level status was never flipped) — this is an idempotent reset, not
    # a real transition, so it intentionally bypasses the state machine.
    retry_history = list(run_ctx.get("dispatch_attempt_history") or [])
    retry_history.append({
        "at": datetime.now(timezone.utc).isoformat(),
        "triggered_by": triggered_by,
        "result_summary": summary,
        "precheck": precheck,
        "dispatch_state": dispatch_state,
    })
    run_ctx["dispatch_attempt_history"] = retry_history
    pr.ended_at = None
    pr.result_summary = None

    new_dispatch = initial_dispatch_state()
    new_dispatch["requeue_attempts"] = (
        int(dispatch_state.get("requeue_attempts") or 0) + 1
    )
    new_dispatch["enqueue_key"] = f"precheck:{run_id}"
    run_ctx["dispatch_state"] = new_dispatch
    run_ctx["dispatch_host_watcher_admin_states"] = (
        snapshot_dispatch_host_watcher_admin_states(
            db, list(run_ctx.get("dispatch_device_ids") or [])
        )
    )
    pr.run_context = run_ctx
    flag_modified(pr, "run_context")
    record_audit(
        db,
        action="plan_dispatch_retry_requested",
        resource_type="plan_run",
        resource_id=run_id,
        details={
            "triggered_by": triggered_by,
            "attempt": len(retry_history),
            "previous_result": summary,
        },
        username=triggered_by,
    )
    db.commit()

    initialise_precheck_state(run_id, db)

    try:
        enqueue_sync(
            "precheck_and_dispatch_task",
            key=f"precheck:{run_id}",
            timeout=600,
            retries=1,
            required=True,
            plan_run_id=run_id,
        )
    except EnqueueSyncError as exc:
        now = datetime.now(timezone.utc)
        pr = db.get(PlanRun, run_id)
        if pr is not None:
            PlanRunStateMachine.transition(pr, PlanRunStatus.FAILED, reason="dispatch_queue_unavailable")
            pr.ended_at = now
            run_ctx = dict(pr.run_context or {})
            dispatch_state = dict(run_ctx.get("dispatch_state") or {})
            dispatch_state["status"] = "failed"
            dispatch_state["last_error"] = str(exc)
            dispatch_state["completed_at"] = utc_iso()
            run_ctx["dispatch_state"] = dispatch_state
            pr.run_context = run_ctx
            pr.result_summary = {
                "precheck_failed": True,
                "reason": "dispatch_queue_unavailable",
                "error": str(exc),
            }
            flag_modified(pr, "run_context")
            record_audit(
                db,
                action="plan_dispatch_retry_enqueue_failed",
                resource_type="plan_run",
                resource_id=run_id,
                details={"triggered_by": triggered_by, "error": str(exc)},
                username=triggered_by,
            )
            db.commit()
        raise PlanRunDispatchRetryError(
            f"dispatch queue unavailable: {exc}"
        ) from exc

    logger.info(
        "plan_run_dispatch_retry plan_run=%d triggered_by=%s",
        run_id,
        triggered_by,
    )
    return {
        "plan_run_id": run_id,
        "status": "RUNNING",
        "dispatch_state": new_dispatch,
    }


async def precheck_and_dispatch_task(ctx: dict, *, plan_run_id: int) -> None:
    """SAQ task: run dispatch gate for ``plan_run_id``."""
    logger.info("saq_precheck_and_dispatch_start plan_run=%d", plan_run_id)
    try:
        await drive_dispatch_gate(plan_run_id)
    except Exception:
        logger.exception("saq_precheck_and_dispatch_failed plan_run=%d", plan_run_id)
        raise
    logger.info("saq_precheck_and_dispatch_done plan_run=%d", plan_run_id)
