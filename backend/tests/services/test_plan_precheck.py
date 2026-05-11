"""ADR-0021 C3 — Dispatch gate (precheck) integration tests."""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from backend.models.enums import HostStatus, JobStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.models.script import Script
from backend.services.plan_dispatcher_sync import prepare_plan_run
from backend.services.plan_precheck import _drive_dispatch_gate


# ---------------------------------------------------------------------------
# Fixtures: Plan + Script + Host + Device chain ready for dispatch
# ---------------------------------------------------------------------------


@pytest.fixture
def gate_chain(db_session):
    host_a = Host(id="h-A", hostname="agentA", status=HostStatus.ONLINE.value, ip="10.0.0.1")
    host_b = Host(id="h-B", hostname="agentB", status=HostStatus.ONLINE.value, ip="10.0.0.2")
    dev_a = Device(serial="dev-A", host_id="h-A", status="ONLINE")
    dev_b = Device(serial="dev-B", host_id="h-B", status="ONLINE")
    script = Script(
        name="check_device", script_type="python", version="1.0.0",
        nfs_path="/scripts/check_device/v1.0.0/check_device.py",
        content_sha256="aabbcc11", default_params={"timeout": 30},
    )
    plan = Plan(name="precheck-plan", patrol_interval_seconds=60)
    db_session.add_all([host_a, host_b, dev_a, dev_b, script, plan])
    db_session.commit()
    db_session.add(PlanStep(
        plan_id=plan.id, step_key="init_check",
        script_name="check_device", script_version="1.0.0",
        stage="init", sort_order=0, timeout_seconds=30, retry=0,
    ))
    db_session.commit()
    return {
        "plan": plan,
        "host_a": host_a, "host_b": host_b,
        "device_a": dev_a, "device_b": dev_b,
        "script": script,
    }


def _ack_ok(host_id: str, expected_sha: str) -> dict:
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


def _ack_drift(host_id: str, expected_sha: str) -> dict:
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


def _prepare_run(db_session, gate_chain) -> PlanRun:
    pr = prepare_plan_run(
        plan_id=gate_chain["plan"].id,
        device_ids=[gate_chain["device_a"].id, gate_chain["device_b"].id],
        triggered_by="testuser",
        db=db_session,
        run_type="MANUAL",
    )
    return pr


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


class TestDispatchGate:
    def test_no_drift_dispatches_jobs(self, db_session, gate_chain):
        pr = _prepare_run(db_session, gate_chain)

        async def _fake_call(host_id, event, data, *, timeout=10.0):
            return _ack_ok(host_id, "aabbcc11")

        with patch(
            "backend.services.plan_precheck.call_agent_rpc",
            side_effect=_fake_call,
        ):
            asyncio.run(_drive_dispatch_gate(pr.id, db=db_session))

        db_session.expire_all()
        pr_after = db_session.get(PlanRun, pr.id)
        assert pr_after.status == "RUNNING"
        precheck = pr_after.run_context["precheck"]
        assert precheck["phase"] == "ready"
        assert precheck["final_result"] == "ready"
        assert precheck["completed_at"] is not None
        assert precheck["hosts"]["h-A"]["status"] == "ok"
        assert precheck["hosts"]["h-B"]["status"] == "ok"

        jobs = db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id
        ).all()
        assert len(jobs) == 2
        assert {j.host_id for j in jobs} == {"h-A", "h-B"}
        assert all(j.status == JobStatus.PENDING.value for j in jobs)

    def test_one_host_drift_resync_and_dispatch(self, db_session, gate_chain):
        pr = _prepare_run(db_session, gate_chain)

        verify_call = {"count": 0}

        async def _fake_call(host_id, event, data, *, timeout=10.0):
            verify_call["count"] += 1
            if host_id == "h-A":
                return _ack_ok(host_id, "aabbcc11")
            # h-B drifts on first verify, aligns on re-verify
            if verify_call["count"] <= 2:
                return _ack_drift(host_id, "aabbcc11")
            return _ack_ok(host_id, "aabbcc11")

        with patch(
            "backend.services.plan_precheck.call_agent_rpc",
            side_effect=_fake_call,
        ), patch(
            "backend.services.plan_precheck._sync_host_via_hot_update",
            return_value=(True, None),
        ), patch(
            "backend.services.plan_precheck.SYNC_SETTLE_SECONDS", 0
        ):
            asyncio.run(_drive_dispatch_gate(pr.id, db=db_session))

        db_session.expire_all()
        pr_after = db_session.get(PlanRun, pr.id)
        precheck = pr_after.run_context["precheck"]
        assert precheck["final_result"] == "ready"
        assert precheck["hosts"]["h-B"]["status"] == "ok"
        assert precheck["hosts"]["h-B"]["sync_attempts"] == 1
        assert precheck["hosts"]["h-B"]["synced_at"] is not None
        assert pr_after.status == "RUNNING"

        jobs = db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id
        ).count()
        assert jobs == 2

    def test_agent_offline_terminal_failure(self, db_session, gate_chain):
        pr = _prepare_run(db_session, gate_chain)

        from backend.realtime.socketio_server import AgentNotConnectedError

        async def _fake_call(host_id, event, data, *, timeout=10.0):
            if host_id == "h-A":
                return _ack_ok(host_id, "aabbcc11")
            raise AgentNotConnectedError(host_id)

        sync_called = {"count": 0}

        def _fake_sync(host_id, db):
            sync_called["count"] += 1
            return (True, None)

        with patch(
            "backend.services.plan_precheck.call_agent_rpc",
            side_effect=_fake_call,
        ), patch(
            "backend.services.plan_precheck._sync_host_via_hot_update",
            side_effect=_fake_sync,
        ):
            asyncio.run(_drive_dispatch_gate(pr.id, db=db_session))

        db_session.expire_all()
        pr_after = db_session.get(PlanRun, pr.id)
        assert pr_after.status == "FAILED"
        assert pr_after.result_summary["precheck_failed"] is True
        assert "agent_offline" in pr_after.result_summary["reason"]

        precheck = pr_after.run_context["precheck"]
        assert precheck["phase"] == "failed"
        assert precheck["final_result"] == "failed"
        assert precheck["hosts"]["h-B"]["error"] == "agent_offline"
        # offline detected before sync — sync must NOT have been triggered.
        assert sync_called["count"] == 0

        jobs = db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id
        ).count()
        assert jobs == 0

    def test_sync_failed_marks_plan_run_failed(self, db_session, gate_chain):
        pr = _prepare_run(db_session, gate_chain)

        async def _fake_call(host_id, event, data, *, timeout=10.0):
            return _ack_drift(host_id, "aabbcc11")  # both drift

        def _fake_sync_fail(host_id, db):
            return (False, "no_ssh_credentials")

        with patch(
            "backend.services.plan_precheck.call_agent_rpc",
            side_effect=_fake_call,
        ), patch(
            "backend.services.plan_precheck._sync_host_via_hot_update",
            side_effect=_fake_sync_fail,
        ):
            asyncio.run(_drive_dispatch_gate(pr.id, db=db_session))

        db_session.expire_all()
        pr_after = db_session.get(PlanRun, pr.id)
        assert pr_after.status == "FAILED"
        assert "sync_failed" in pr_after.result_summary["reason"]

        precheck = pr_after.run_context["precheck"]
        assert precheck["final_result"] == "failed"
        assert precheck["hosts"]["h-A"]["error"] == "no_ssh_credentials"
        assert precheck["hosts"]["h-B"]["error"] == "no_ssh_credentials"

    def test_reverify_still_failing_marks_plan_run_failed(
        self, db_session, gate_chain
    ):
        pr = _prepare_run(db_session, gate_chain)

        async def _fake_call(host_id, event, data, *, timeout=10.0):
            # always returns drift — re-verify after sync still bad
            return _ack_drift(host_id, "aabbcc11")

        with patch(
            "backend.services.plan_precheck.call_agent_rpc",
            side_effect=_fake_call,
        ), patch(
            "backend.services.plan_precheck._sync_host_via_hot_update",
            return_value=(True, None),
        ), patch(
            "backend.services.plan_precheck.SYNC_SETTLE_SECONDS", 0
        ):
            asyncio.run(_drive_dispatch_gate(pr.id, db=db_session))

        db_session.expire_all()
        pr_after = db_session.get(PlanRun, pr.id)
        assert pr_after.status == "FAILED"
        assert "reverify_failed" in pr_after.result_summary["reason"]
        precheck = pr_after.run_context["precheck"]
        assert precheck["final_result"] == "failed"

    def test_skip_when_plan_run_not_running(self, db_session, gate_chain):
        pr = _prepare_run(db_session, gate_chain)
        pr.status = "FAILED"
        db_session.commit()

        async def _fake_call(host_id, event, data, *, timeout=10.0):
            pytest.fail("call_agent_rpc must not be invoked when status != RUNNING")

        with patch(
            "backend.services.plan_precheck.call_agent_rpc",
            side_effect=_fake_call,
        ):
            asyncio.run(_drive_dispatch_gate(pr.id, db=db_session))

    def test_no_scripts_in_snapshot_marks_failed(self, db_session, gate_chain):
        pr = _prepare_run(db_session, gate_chain)

        # Wipe scripts so _expected_scripts_for_run returns []
        db_session.query(Script).delete()
        db_session.commit()

        with patch(
            "backend.services.plan_precheck.call_agent_rpc",
            new_callable=AsyncMock,
        ) as mock_call:
            asyncio.run(_drive_dispatch_gate(pr.id, db=db_session))
            mock_call.assert_not_called()

        db_session.expire_all()
        pr_after = db_session.get(PlanRun, pr.id)
        assert pr_after.status == "FAILED"
        assert "no_scripts_resolved" in pr_after.result_summary["reason"]


# ---------------------------------------------------------------------------
# /plans/{id}/run endpoint integration: enqueues without dispatching
# ---------------------------------------------------------------------------


class TestRunPlanEndpointEnqueues:
    def test_run_plan_creates_plan_run_and_enqueues(
        self, client, auth_headers, db_session, gate_chain
    ):
        plan_id = gate_chain["plan"].id
        device_ids = [gate_chain["device_a"].id]

        with patch("backend.api.routes.plans.enqueue_sync") as enq:
            resp = client.post(
                f"/api/v1/plans/{plan_id}/run",
                json={"device_ids": device_ids},
                headers=auth_headers,
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["status"] == "RUNNING"
        assert data["run_type"] == "MANUAL"
        assert data["run_context"]["dispatch_device_ids"] == device_ids
        # No JobInstances yet — gate hasn't run.
        jobs = db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == data["id"]
        ).count()
        assert jobs == 0

        enq.assert_called_once()
        kwargs = enq.call_args.kwargs
        assert enq.call_args.args[0] == "precheck_and_dispatch_task"
        assert kwargs["plan_run_id"] == data["id"]
        assert kwargs["key"] == f"precheck:{data['id']}"


# ---------------------------------------------------------------------------
# dispatch_state structure (ADR-0021 audit)
# ---------------------------------------------------------------------------


class TestDispatchStatePersistence:
    def test_run_plan_records_dispatch_state(
        self, client, auth_headers, db_session, gate_chain
    ):
        plan_id = gate_chain["plan"].id
        device_ids = [gate_chain["device_a"].id]

        with patch("backend.api.routes.plans.enqueue_sync"):
            resp = client.post(
                f"/api/v1/plans/{plan_id}/run",
                json={"device_ids": device_ids},
                headers=auth_headers,
            )

        assert resp.status_code == 200, resp.text
        plan_run_id = resp.json()["data"]["id"]

        pr = db_session.get(PlanRun, plan_run_id)
        assert pr is not None

        state = pr.run_context["dispatch_state"]
        assert state["enqueue_key"] == f"precheck:{plan_run_id}"
        assert state["requeue_attempts"] == 0
        assert state["status"] == "queued"
        assert state["enqueued_at"] is not None
        datetime.strptime(state["enqueued_at"], "%Y-%m-%dT%H:%M:%S.%fZ")
        assert state["started_at"] is None
        assert state["completed_at"] is None
        assert state["last_error"] is None

        # dispatch_device_ids must still be present
        assert pr.run_context["dispatch_device_ids"] == device_ids

    def test_gate_updates_dispatch_state_on_run(
        self, db_session, gate_chain
    ):
        pr = _prepare_run(db_session, gate_chain)
        # Simulate dispatch_state being seeded (as API would do)
        run_ctx = dict(pr.run_context or {})
        run_ctx["dispatch_state"] = {
            "enqueue_key": f"precheck:{pr.id}",
            "requeue_attempts": 0,
            "status": "queued",
            "enqueued_at": "2026-05-10T07:00:00.000Z",
            "started_at": None,
            "completed_at": None,
            "last_error": None,
        }
        pr.run_context = run_ctx
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(pr, "run_context")
        db_session.commit()

        async def _fake_call(host_id, event, data, *, timeout=10.0):
            return _ack_ok(host_id, "aabbcc11")

        with patch(
            "backend.services.plan_precheck.call_agent_rpc",
            side_effect=_fake_call,
        ):
            asyncio.run(_drive_dispatch_gate(pr.id, db=db_session))

        db_session.expire_all()
        pr_after = db_session.get(PlanRun, pr.id)
        state = pr_after.run_context["dispatch_state"]
        assert state["status"] == "completed"
        assert state["started_at"] is not None
        assert state["completed_at"] is not None
        assert state["last_error"] is None
