"""Dispatch idempotency metadata persisted on PlanRun.run_context."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.models.plan_run import PlanRun


def compute_idempotency_key(plan_run_id: int) -> str:
    return f"plan_run_dispatch:{plan_run_id}"


def compute_dispatch_payload_hash(pr: PlanRun) -> str:
    run_ctx = pr.run_context if isinstance(pr.run_context, dict) else {}
    snapshot = pr.plan_snapshot if isinstance(pr.plan_snapshot, dict) else {}
    payload = {
        "plan_id": pr.plan_id,
        "plan_run_id": pr.id,
        "device_ids": sorted(run_ctx.get("dispatch_device_ids") or []),
        "steps": sorted(
            (s.get("script_name"), s.get("script_version"))
            for s in (snapshot.get("steps") or [])
        ),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def persist_dispatch_idempotency(pr: PlanRun, db: Session) -> None:
    """Write idempotency_key + dispatch_payload_hash into run_context."""
    run_ctx = dict(pr.run_context or {})
    run_ctx["idempotency_key"] = compute_idempotency_key(pr.id)
    run_ctx["dispatch_payload_hash"] = compute_dispatch_payload_hash(pr)
    pr.run_context = run_ctx
    flag_modified(pr, "run_context")
    db.commit()
    db.refresh(pr)
