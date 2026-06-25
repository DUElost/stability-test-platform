"""Shared helpers for dispatch-gate (precheck) service tests."""

from __future__ import annotations

from backend.models.plan_run import PlanRun
from backend.services.plan_dispatcher_sync import prepare_plan_run


def ack_ok(host_id: str, expected_sha: str) -> dict:
    return {
        "host_id": host_id,
        "agent_version": "test",
        "results": [
            {
                "name": "check_device",
                "version": "1.0.0",
                "expected_sha": expected_sha,
                "actual_sha": expected_sha,
                "exists": True,
                "ok": True,
                "error": None,
            }
        ],
        "checked_at": "2026-05-07T10:00:00Z",
    }


def ack_drift(host_id: str, expected_sha: str) -> dict:
    return {
        "host_id": host_id,
        "agent_version": "test",
        "results": [
            {
                "name": "check_device",
                "version": "1.0.0",
                "expected_sha": expected_sha,
                "actual_sha": "deadbeef",
                "exists": True,
                "ok": False,
                "error": None,
            }
        ],
        "checked_at": "2026-05-07T10:00:00Z",
    }


def prepare_two_host_run(db_session, gate_chain) -> PlanRun:
    return prepare_plan_run(
        plan_id=gate_chain["plan"].id,
        device_ids=[gate_chain["device_a"].id, gate_chain["device_b"].id],
        triggered_by="testuser",
        db=db_session,
        run_type="MANUAL",
    )
