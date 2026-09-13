"""ADR-0021 — PlanRun.run_context.precheck schema + PlanRunDetailOut wire format."""

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
# PlanRunDetailOut returns run_context.precheck verbatim
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


class TestPlanRunDetailOutCarriesRunContext:
    def test_get_plan_run_returns_run_context_precheck(
        self, client, auth_headers, db_session
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

        resp = client.get(f"/api/v1/plan-runs/{pr.id}", headers=auth_headers)
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

    def test_list_plan_runs_includes_run_context(self, client, auth_headers, db_session):
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

        resp = client.get(f"/api/v1/plan-runs?plan_id={plan.id}", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        payload = resp.json()["data"]
        assert payload["total"] == 1
        assert payload["stats"]["total"] == 1
        items = payload["items"]
        assert len(items) == 1
        assert items[0]["run_context"]["precheck"]["phase"] == "verifying"
        assert items[0]["device_count"] == 0

    def test_list_plan_runs_includes_device_count(
        self, client, auth_headers, db_session, sample_running_job
    ):
        pr_id = sample_running_job.plan_run_id
        resp = client.get(f"/api/v1/plan-runs?plan_id={sample_running_job.plan_id}", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        items = resp.json()["data"]["items"]
        match = next(i for i in items if i["id"] == pr_id)
        assert match["device_count"] >= 1

    def test_plan_run_out_device_count_dedupes_job_device_ids(self):
        """#747: _plan_run_out fallback uses distinct device_id (not job rows)."""
        from backend.api.routes.plan_runs import _plan_run_out
        from backend.api.schemas.plan_run import JobInstanceOut
        from backend.models.enums import PlanRunStatus

        pr = PlanRun(
            id=42,
            plan_id=7,
            status=PlanRunStatus.RUNNING.value,
            failure_threshold=0.05,
            plan_snapshot={"plan_id": 7, "steps": []},
            run_type="MANUAL",
            triggered_by="test",
            chain_index=0,
            started_at=datetime.now(timezone.utc),
        )
        jobs = [
            JobInstanceOut(
                id=1, plan_run_id=42, plan_id=7, device_id=11, status="RUNNING",
            ),
            JobInstanceOut(
                id=2, plan_run_id=42, plan_id=7, device_id=11, status="PENDING",
            ),
            JobInstanceOut(
                id=3, plan_run_id=42, plan_id=7, device_id=22, status="RUNNING",
            ),
        ]
        out = _plan_run_out(pr, jobs=jobs)
        assert len(out.jobs) == 3
        assert out.device_count == 2

    def test_list_and_detail_device_count_match_for_two_devices(
        self, client, auth_headers, db_session, sample_running_job, sample_host
    ):
        """#747: list distinct count agrees with detail for multi-device runs."""
        from backend.models.enums import DeviceStatus, JobStatus
        from backend.models.host import Device
        from backend.models.job import JobInstance

        other = Device(
            serial=f"dev-747-{datetime.now(timezone.utc).timestamp()}",
            model="TEST",
            status=DeviceStatus.ONLINE.value,
            host_id=sample_host.id,
        )
        db_session.add(other)
        db_session.flush()
        db_session.add(
            JobInstance(
                plan_run_id=sample_running_job.plan_run_id,
                plan_id=sample_running_job.plan_id,
                device_id=other.id,
                host_id=sample_host.id,
                status=JobStatus.PENDING.value,
                pipeline_def={"lifecycle": {"init": [], "teardown": []}},
            )
        )
        db_session.commit()

        list_resp = client.get(
            f"/api/v1/plan-runs?plan_id={sample_running_job.plan_id}",
            headers=auth_headers,
        )
        assert list_resp.status_code == 200, list_resp.text
        match = next(
            i
            for i in list_resp.json()["data"]["items"]
            if i["id"] == sample_running_job.plan_run_id
        )
        assert match["device_count"] == 2

        detail_resp = client.get(
            f"/api/v1/plan-runs/{sample_running_job.plan_run_id}",
            headers=auth_headers,
        )
        assert detail_resp.status_code == 200, detail_resp.text
        detail = detail_resp.json()["data"]
        assert len(detail["jobs"]) == 2
        assert detail["device_count"] == 2

    def test_plan_run_without_run_context_serialises_to_null(
        self, client, auth_headers, db_session
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

        resp = client.get(f"/api/v1/plan-runs/{pr.id}", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["run_context"] is None
        assert body["chain_index"] == 0

    def test_list_plan_runs_rejects_invalid_status_filter(
        self, client, auth_headers, db_session
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

        resp = client.get("/api/v1/plan-runs?status=PENDING", headers=auth_headers)

        assert resp.status_code == 422, resp.text


class TestJobExecutionStateObservability:
    def test_plan_run_job_apis_return_execution_clocks(
        self,
        client,
        auth_headers,
        db_session,
        sample_running_job,
    ):
        execution_heartbeat_at = datetime(
            2026, 7, 23, 10, 11, 12, tzinfo=timezone.utc,
        )
        progress_at = datetime(
            2026, 7, 23, 10, 12, 13, tzinfo=timezone.utc,
        )
        sample_running_job.execution_state = "WAITING_EXECUTION_SLOT"
        sample_running_job.last_execution_heartbeat_at = execution_heartbeat_at
        sample_running_job.last_progress_at = progress_at
        db_session.commit()

        detail_response = client.get(
            f"/api/v1/plan-runs/{sample_running_job.plan_run_id}",
            headers=auth_headers,
        )
        assert detail_response.status_code == 200, detail_response.text
        detail_job = detail_response.json()["data"]["jobs"][0]

        jobs_response = client.get(
            f"/api/v1/plan-runs/{sample_running_job.plan_run_id}/jobs",
            headers=auth_headers,
        )
        assert jobs_response.status_code == 200, jobs_response.text
        listed_job = jobs_response.json()["data"][0]

        for job in (detail_job, listed_job):
            assert job["execution_state"] == "WAITING_EXECUTION_SLOT"
            assert job["last_execution_heartbeat_at"] == execution_heartbeat_at.isoformat()
            assert job["last_progress_at"] == progress_at.isoformat()
