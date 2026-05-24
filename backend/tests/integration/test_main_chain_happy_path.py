"""Main-chain happy-path smoke tests (prepare → precheck gate → dispatch)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from backend.models.enums import JobStatus
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun
from backend.services.aggregator_sync import plan_aggregator_sync
from backend.services.plan_dispatcher_sync import (
    dispatch_plan_sync,
    initial_dispatch_state,
    prepare_plan_run,
)
from backend.services.plan_precheck import _drive_dispatch_gate


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


class TestManualDispatchHappyPath:
    """MANUAL: prepare only → async gate materialises jobs + dispatch_state."""

    def test_prepare_then_gate_transitions(self, db_session, gate_chain):
        pr = prepare_plan_run(
            plan_id=gate_chain["plan"].id,
            device_ids=[gate_chain["device_a"].id],
            triggered_by="integration-test",
            db=db_session,
            run_type="MANUAL",
            run_context={"dispatch_state": initial_dispatch_state()},
        )
        assert pr.run_context["dispatch_state"]["status"] == "queued"
        assert (
            db_session.query(JobInstance)
            .filter(JobInstance.plan_run_id == pr.id)
            .count()
            == 0
        )

        async def _fake_call(host_id, event, data, *, timeout=10.0):
            return _ack_ok(host_id, gate_chain["script"].content_sha256)

        with patch(
            "backend.services.plan_precheck.call_agent_rpc",
            side_effect=_fake_call,
        ):
            asyncio.run(_drive_dispatch_gate(pr.id, db=db_session))

        db_session.expire_all()
        refreshed = db_session.get(PlanRun, pr.id)
        assert refreshed.status == "RUNNING"
        assert refreshed.run_context["precheck"]["phase"] == "ready"
        assert refreshed.run_context["dispatch_state"]["status"] == "completed"
        assert refreshed.run_context["dispatch_state"]["started_at"] is not None
        assert refreshed.run_context["dispatch_state"]["completed_at"] is not None

        jobs = (
            db_session.query(JobInstance)
            .filter(JobInstance.plan_run_id == pr.id)
            .all()
        )
        assert len(jobs) == 1
        assert jobs[0].status == JobStatus.PENDING.value


class TestChainScheduleDispatchHappyPath:
    """CHAIN / SCHEDULE: dispatch_plan_sync runs gate inline."""

    @pytest.mark.parametrize("run_type", ["CHAIN", "SCHEDULE"])
    def test_sync_dispatch_runs_gate(self, db_session, gate_chain, run_type):
        async def _fake_call(host_id, event, data, *, timeout=10.0):
            return _ack_ok(host_id, gate_chain["script"].content_sha256)

        with patch(
            "backend.services.plan_precheck.call_agent_rpc",
            side_effect=_fake_call,
        ):
            pr = dispatch_plan_sync(
                plan_id=gate_chain["plan"].id,
                device_ids=[gate_chain["device_a"].id, gate_chain["device_b"].id],
                triggered_by="integration-test",
                db=db_session,
                run_type=run_type,
            )

        assert pr.run_type == run_type
        assert pr.run_context["precheck"]["phase"] == "ready"
        assert pr.run_context["dispatch_state"]["status"] == "completed"
        jobs = (
            db_session.query(JobInstance)
            .filter(JobInstance.plan_run_id == pr.id)
            .count()
        )
        assert jobs == 2


class TestJobCompleteAggregationPath:
    """Gate → materialised jobs → terminal job → PlanRun aggregation."""

    def test_complete_job_aggregates_plan_run_success(self, db_session, gate_chain):
        async def _fake_call(host_id, event, data, *, timeout=10.0):
            return _ack_ok(host_id, gate_chain["script"].content_sha256)

        with patch(
            "backend.services.plan_precheck.call_agent_rpc",
            side_effect=_fake_call,
        ):
            pr = dispatch_plan_sync(
                plan_id=gate_chain["plan"].id,
                device_ids=[gate_chain["device_a"].id],
                triggered_by="integration-test",
                db=db_session,
                run_type="MANUAL",
            )

        jobs = (
            db_session.query(JobInstance)
            .filter(JobInstance.plan_run_id == pr.id)
            .all()
        )
        assert len(jobs) == 1
        job = jobs[0]
        assert job.status == JobStatus.PENDING.value

        # Simulate Agent claim + successful completion (no external I/O).
        job.status = JobStatus.COMPLETED.value
        job.ended_at = datetime.now(timezone.utc)
        db_session.commit()

        plan_aggregator_sync(job, db_session)
        db_session.expire_all()

        refreshed = db_session.get(PlanRun, pr.id)
        assert refreshed.status == "SUCCESS"
        assert refreshed.ended_at is not None
        assert refreshed.result_summary is not None
        assert refreshed.result_summary["completed"] == 1
        assert refreshed.result_summary["total"] == 1
