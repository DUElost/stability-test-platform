"""Precheck + dispatch_state persistence helpers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.core.audit import record_audit
from backend.models.enums import PlanRunStatus
from backend.models.host import Device
from backend.models.plan_run import PlanRun
from backend.services.plan_dispatcher_sync import PlanDispatchError
from backend.services.state_machine import PlanRunStateMachine

from . import DISPATCH_SYNC_MAX_ATTEMPTS, utc_iso
from .notify import emit_dispatch_gate_invalidation

logger = logging.getLogger(__name__)


def initial_precheck_state(host_ids: list[str]) -> dict:
    return {
        "phase": "verifying",
        "started_at": utc_iso(),
        "completed_at": None,
        "hosts": {
            hid: {
                "status": "pending",
                "checked_at": None,
                "synced_at": None,
                "scripts": [],
                "sync_attempts": 0,
                "error": None,
            }
            for hid in host_ids
        },
        "final_result": None,
        "errors": [],
        "sync_max_attempts": DISPATCH_SYNC_MAX_ATTEMPTS,
    }


_initial_precheck_state = initial_precheck_state


def initialise_precheck_state(plan_run_id: int, db: Session) -> dict:
    pr = db.get(PlanRun, plan_run_id)
    if pr is None:
        raise PlanDispatchError(f"PlanRun {plan_run_id} not found")

    run_ctx = dict(pr.run_context or {})
    device_ids = list(run_ctx.get("dispatch_device_ids") or [])
    if not device_ids:
        raise PlanDispatchError(
            f"PlanRun {plan_run_id}: run_context.dispatch_device_ids is empty"
        )

    device_rows = db.execute(
        select(Device.id, Device.host_id).where(Device.id.in_(device_ids))
    ).all()
    host_ids = sorted({row.host_id for row in device_rows if row.host_id})

    if not host_ids:
        raise PlanDispatchError(
            f"PlanRun {plan_run_id}: no hosts resolved from devices"
        )

    precheck = initial_precheck_state(host_ids)
    run_ctx["precheck"] = precheck
    pr.run_context = run_ctx
    flag_modified(pr, "run_context")
    db.commit()
    db.refresh(pr)
    logger.info(
        "precheck_initialised plan_run=%d hosts=%s", plan_run_id, host_ids
    )
    return precheck


def persist_precheck(plan_run_id: int, precheck: dict, db: Session) -> None:
    pr = db.get(PlanRun, plan_run_id)
    if pr is None:
        return
    run_ctx = dict(pr.run_context or {})
    run_ctx["precheck"] = precheck
    pr.run_context = run_ctx
    flag_modified(pr, "run_context")
    db.commit()
    emit_dispatch_gate_invalidation(plan_run_id, phase=precheck.get("phase"))


_persist_precheck = persist_precheck


def update_dispatch_state(pr: PlanRun, db: Session, **patch: object) -> None:
    run_ctx = dict(pr.run_context or {})
    state = dict(run_ctx.get("dispatch_state") or {})
    state.update(patch)
    run_ctx["dispatch_state"] = state
    pr.run_context = run_ctx
    flag_modified(pr, "run_context")
    db.commit()
    dispatch_status = state.get("status")
    if isinstance(dispatch_status, str):
        precheck = run_ctx.get("precheck") or {}
        phase = precheck.get("phase") if isinstance(precheck, dict) else None
        emit_dispatch_gate_invalidation(
            pr.id,
            phase=phase if isinstance(phase, str) else None,
            dispatch_status=dispatch_status,
        )


_update_dispatch_state = update_dispatch_state


def mark_precheck_failed(
    plan_run_id: int,
    precheck: dict,
    db: Session,
    *,
    error: str,
    code: str | None = None,
    inactive_host_ids: list[str] | None = None,
) -> None:
    precheck["phase"] = "failed"
    precheck["final_result"] = "failed"
    precheck["completed_at"] = utc_iso()
    precheck.setdefault("errors", []).append(error)
    if code:
        precheck["gate_failure"] = {
            "code": code,
            "message": error,
            "inactive_host_ids": list(inactive_host_ids or []),
        }

    pr = db.get(PlanRun, plan_run_id)
    if pr is None:
        return
    run_ctx = dict(pr.run_context or {})
    run_ctx["precheck"] = precheck
    pr.run_context = run_ctx
    PlanRunStateMachine.transition(pr, PlanRunStatus.FAILED, reason=error)
    pr.ended_at = datetime.now(timezone.utc)
    pr.result_summary = {
        "precheck_failed": True,
        "reason": error,
    }
    if code:
        pr.result_summary["code"] = code
    if inactive_host_ids:
        pr.result_summary["inactive_host_ids"] = list(inactive_host_ids)
    flag_modified(pr, "run_context")

    dispatch_state = dict(run_ctx.get("dispatch_state") or {})
    dispatch_state["status"] = "failed"
    dispatch_state["completed_at"] = utc_iso()
    dispatch_state["last_error"] = (
        f"precheck:{code}" if code else f"precheck:{error}"
    )
    run_ctx["dispatch_state"] = dispatch_state

    record_audit(
        db,
        action="plan_dispatch_gate_failed",
        resource_type="plan_run",
        resource_id=plan_run_id,
        details={
            "error": error,
            "code": code,
            "inactive_host_ids": list(inactive_host_ids or []),
        },
        username="system",
    )
    db.commit()
    emit_dispatch_gate_invalidation(
        plan_run_id,
        phase="failed",
        dispatch_status="failed",
    )
    logger.info("precheck_failed plan_run=%d error=%s", plan_run_id, error)


_mark_precheck_failed = mark_precheck_failed
