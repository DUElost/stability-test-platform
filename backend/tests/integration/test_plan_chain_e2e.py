"""Plan chain dispatch full-path integration (T-B10)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from backend.models.enums import JobStatus
from backend.models.job import JobInstance
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.services.aggregator_sync import plan_aggregator_sync
from backend.services.plan_chain_trigger import trigger_next_plan_sync
from backend.services.plan_dispatcher_core import PlanDispatchError
from backend.services.plan_dispatcher_sync import dispatch_plan_sync
from backend.services.precheck.runner import precheck_and_dispatch_task

pytestmark = pytest.mark.integration


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


def _seed_chain_plans(db_session, gate_chain):
    child_plan = Plan(name="chain-child")
    parent_plan = gate_chain["plan"]
    parent_plan.next_plan_id = None
    db_session.add(child_plan)
    db_session.flush()
    parent_plan.next_plan_id = child_plan.id
    db_session.add(
        PlanStep(
            plan_id=child_plan.id,
            step_key="init_check",
            script_name="check_device",
            script_version="1.0.0",
            stage="init",
            sort_order=0,
            timeout_seconds=30,
            retry=0,
        )
    )
    db_session.commit()
    return parent_plan, child_plan


def _complete_parent_and_aggregate(db_session, parent_run):
    job = (
        db_session.query(JobInstance)
        .filter(JobInstance.plan_run_id == parent_run.id)
        .one()
    )
    job.status = JobStatus.COMPLETED.value
    job.ended_at = datetime.now(timezone.utc)
    db_session.commit()
    plan_aggregator_sync(job, db_session)
    db_session.commit()


class TestPlanChainDispatchE2E:
    def test_parent_success_triggers_child_plan_run_with_gate(
        self, db_session, gate_chain,
    ):
        parent_plan, child_plan = _seed_chain_plans(db_session, gate_chain)

        async def _fake_call(host_id, event, data, *, timeout=10.0):
            return _ack_ok(host_id, gate_chain["script"].content_sha256)

        enqueued: list[tuple[str, dict]] = []
        with patch(
            "backend.services.precheck.verify.call_agent_rpc",
            side_effect=_fake_call,
        ), patch(
            "backend.services.plan_chain_trigger.enqueue_sync",
            side_effect=lambda task, **kwargs: enqueued.append((task, kwargs)),
        ):
            parent_run = dispatch_plan_sync(
                plan_id=parent_plan.id,
                device_ids=[gate_chain["device_a"].id],
                triggered_by="integration-test",
                db=db_session,
                run_type="MANUAL",
            )
            _complete_parent_and_aggregate(db_session, parent_run)
            assert enqueued[0][0] == "precheck_and_dispatch_task"
            asyncio.run(
                precheck_and_dispatch_task(
                    {}, plan_run_id=enqueued[0][1]["plan_run_id"],
                ),
            )

        db_session.expire_all()
        parent_refreshed = db_session.get(PlanRun, parent_run.id)
        assert parent_refreshed.status == "SUCCESS"
        assert parent_refreshed.next_plan_triggered is True

        child_run = (
            db_session.query(PlanRun)
            .filter(PlanRun.parent_plan_run_id == parent_run.id)
            .filter(PlanRun.plan_id == child_plan.id)
            .one()
        )
        assert child_run.run_type == "CHAIN"
        assert child_run.run_context["precheck"]["phase"] == "ready"
        assert child_run.run_context["dispatch_state"]["status"] == "completed"
        child_jobs = (
            db_session.query(JobInstance)
            .filter(JobInstance.plan_run_id == child_run.id)
            .count()
        )
        assert child_jobs == 1

    def test_chain_dispatch_failure_rolls_back_next_plan_triggered(
        self, db_session, gate_chain,
    ):
        parent_plan, _child_plan = _seed_chain_plans(db_session, gate_chain)

        async def _fake_call(host_id, event, data, *, timeout=10.0):
            return _ack_ok(host_id, gate_chain["script"].content_sha256)

        with patch(
            "backend.services.precheck.verify.call_agent_rpc",
            side_effect=_fake_call,
        ):
            parent_run = dispatch_plan_sync(
                plan_id=parent_plan.id,
                device_ids=[gate_chain["device_a"].id],
                triggered_by="integration-test",
                db=db_session,
                run_type="MANUAL",
            )

        parent_refreshed = db_session.get(PlanRun, parent_run.id)
        parent_refreshed.status = "SUCCESS"
        parent_refreshed.next_plan_triggered = False
        parent_refreshed.result_summary = {"completed": 1, "total": 1}
        db_session.commit()

        with patch(
            "backend.services.plan_chain_trigger.prepare_plan_run",
            side_effect=PlanDispatchError("devices unavailable"),
        ):
            result = trigger_next_plan_sync(parent_refreshed, db_session)

        assert result is None
        db_session.expire_all()
        refreshed = db_session.get(PlanRun, parent_run.id)
        assert refreshed.next_plan_triggered is False
        assert "chain_dispatch_failed" in (refreshed.result_summary or {})
        assert "devices unavailable" in refreshed.result_summary["chain_dispatch_failed"]["error"]
