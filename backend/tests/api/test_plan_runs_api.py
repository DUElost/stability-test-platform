"""ADR-0021 — PlanRun.run_context.precheck schema + PlanRunOut wire format."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.api.schemas.plan_run_precheck import (
    PrecheckHostState,
    PrecheckScriptResult,
    PrecheckSummary,
)
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun


# ---------------------------------------------------------------------------
# PrecheckSummary pydantic schema
# ---------------------------------------------------------------------------


class TestPrecheckSchema:
    def test_default_values_form_a_minimal_in_flight_payload(self):
        s = PrecheckSummary(started_at="2026-05-07T10:00:00Z")
        assert s.phase == "verifying"
        assert s.completed_at is None
        assert s.hosts == {}
        assert s.final_result is None
        assert s.errors == []

    def test_round_trip_through_dict(self):
        s = PrecheckSummary(
            phase="ready",
            started_at="2026-05-07T10:00:00Z",
            completed_at="2026-05-07T10:01:30Z",
            hosts={
                "host-101": PrecheckHostState(
                    status="ok",
                    checked_at="2026-05-07T10:00:02Z",
                    scripts=[
                        PrecheckScriptResult(
                            name="monkey_launch",
                            version="v2.0.0",
                            expected_sha="ab12",
                            actual_sha="ab12",
                            exists=True,
                            ok=True,
                        )
                    ],
                ),
            },
            final_result="ready",
        )
        round_tripped = PrecheckSummary.model_validate(s.model_dump())
        assert round_tripped.phase == "ready"
        assert round_tripped.hosts["host-101"].status == "ok"
        assert round_tripped.hosts["host-101"].scripts[0].ok is True
        assert round_tripped.final_result == "ready"

    def test_rejects_unknown_phase(self):
        with pytest.raises(ValueError):
            PrecheckSummary(phase="weird", started_at="2026-05-07T10:00:00Z")

    def test_rejects_extra_fields(self):
        with pytest.raises(ValueError):
            PrecheckSummary(
                started_at="2026-05-07T10:00:00Z",
                what_is_this="???",
            )

    def test_failed_summary_carries_errors(self):
        s = PrecheckSummary(
            phase="failed",
            started_at="2026-05-07T10:00:00Z",
            completed_at="2026-05-07T10:00:08Z",
            hosts={
                "host-202": PrecheckHostState(
                    status="failed",
                    error="agent_offline",
                    sync_attempts=1,
                ),
            },
            final_result="failed",
            errors=["host-202: agent_offline after sync"],
        )
        assert s.final_result == "failed"
        assert s.hosts["host-202"].error == "agent_offline"
        assert s.hosts["host-202"].sync_attempts == 1


# ---------------------------------------------------------------------------
# PlanRunOut returns run_context.precheck verbatim
# ---------------------------------------------------------------------------


def _create_minimal_plan(db_session) -> Plan:
    plan = Plan(
        name=f"plan_run_ctx_{datetime.now(timezone.utc).timestamp()}",
        description=None,
        failure_threshold=0.05,
        patrol_interval_seconds=60,
        timeout_seconds=300,
        watcher_policy=None,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


class TestPlanRunOutCarriesRunContext:
    def test_get_plan_run_returns_run_context_precheck(
        self, client, db_session
    ):
        plan = _create_minimal_plan(db_session)
        precheck_payload = {
            "phase": "ready",
            "started_at": "2026-05-07T10:00:00Z",
            "completed_at": "2026-05-07T10:01:30Z",
            "hosts": {
                "host-101": {
                    "status": "ok",
                    "checked_at": "2026-05-07T10:00:02Z",
                    "synced_at": None,
                    "scripts": [
                        {
                            "name": "check_device",
                            "version": "1.0.0",
                            "expected_sha": "deadbeef",
                            "actual_sha": "deadbeef",
                            "exists": True,
                            "ok": True,
                            "error": None,
                        }
                    ],
                    "sync_attempts": 0,
                    "error": None,
                }
            },
            "final_result": "ready",
            "errors": [],
        }
        run_ctx = {"precheck": precheck_payload}
        plan_snapshot = {"plan_id": plan.id, "steps": []}

        pr = PlanRun(
            plan_id=plan.id,
            status="RUNNING",
            failure_threshold=0.05,
            plan_snapshot=plan_snapshot,
            run_type="MANUAL",
            run_context=run_ctx,
            triggered_by="testuser",
            chain_index=0,
            started_at=datetime.now(timezone.utc),
        )
        db_session.add(pr)
        db_session.commit()
        db_session.refresh(pr)

        resp = client.get(f"/api/v1/plan-runs/{pr.id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]

        assert body["id"] == pr.id
        assert body["plan_id"] == plan.id
        assert body["status"] == "RUNNING"
        assert body["chain_index"] == 0
        assert body["next_plan_triggered"] is False
        assert body["plan_snapshot"] == plan_snapshot

        assert body["run_context"] is not None
        assert body["run_context"]["precheck"]["phase"] == "ready"
        assert body["run_context"]["precheck"]["final_result"] == "ready"

        host_state = body["run_context"]["precheck"]["hosts"]["host-101"]
        assert host_state["status"] == "ok"
        assert host_state["scripts"][0]["ok"] is True
        assert host_state["scripts"][0]["expected_sha"] == "deadbeef"

        # Validate that the wire payload conforms to PrecheckSummary.
        validated = PrecheckSummary.model_validate(body["run_context"]["precheck"])
        assert validated.hosts["host-101"].scripts[0].name == "check_device"

    def test_list_plan_runs_includes_run_context(self, client, db_session):
        plan = _create_minimal_plan(db_session)
        pr = PlanRun(
            plan_id=plan.id,
            status="RUNNING",
            failure_threshold=0.05,
            plan_snapshot={"plan_id": plan.id, "steps": []},
            run_type="MANUAL",
            run_context={
                "precheck": {
                    "phase": "verifying",
                    "started_at": "2026-05-07T10:00:00Z",
                    "hosts": {},
                    "errors": [],
                }
            },
            triggered_by="testuser",
            chain_index=0,
            started_at=datetime.now(timezone.utc),
        )
        db_session.add(pr)
        db_session.commit()

        resp = client.get(f"/api/v1/plan-runs?plan_id={plan.id}")
        assert resp.status_code == 200, resp.text
        items = resp.json()["data"]
        assert len(items) == 1
        assert items[0]["run_context"]["precheck"]["phase"] == "verifying"

    def test_plan_run_without_run_context_serialises_to_null(
        self, client, db_session
    ):
        plan = _create_minimal_plan(db_session)
        pr = PlanRun(
            plan_id=plan.id,
            status="SUCCESS",
            failure_threshold=0.05,
            plan_snapshot={"plan_id": plan.id, "steps": []},
            run_type="SCHEDULE",
            run_context=None,
            triggered_by="cron",
            chain_index=0,
            started_at=datetime.now(timezone.utc),
        )
        db_session.add(pr)
        db_session.commit()
        db_session.refresh(pr)

        resp = client.get(f"/api/v1/plan-runs/{pr.id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["run_context"] is None
        assert body["chain_index"] == 0

    def test_list_plan_runs_rejects_invalid_status_filter(
        self, client, db_session
    ):
        plan = _create_minimal_plan(db_session)
        pr = PlanRun(
            plan_id=plan.id,
            status="RUNNING",
            failure_threshold=0.05,
            plan_snapshot={"plan_id": plan.id, "steps": []},
            run_type="MANUAL",
            run_context=None,
            triggered_by="testuser",
            chain_index=0,
            started_at=datetime.now(timezone.utc),
        )
        db_session.add(pr)
        db_session.commit()

        resp = client.get("/api/v1/plan-runs?status=PENDING")

        assert resp.status_code == 422, resp.text
