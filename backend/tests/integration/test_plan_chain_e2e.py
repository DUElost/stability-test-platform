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
from backend.services.admission_pump import claim_queued_plan_runs, plan_admission_task

pytestmark = pytest.mark.integration


def _admit_plan_run(db_session, plan_run_id: int) -> None:
    claimed = claim_queued_plan_runs(db_session)
    attempt = next(a for rid, a in claimed if rid == plan_run_id)
    db_session.expire_all()

    async def fake_gather(host_ids, expected):
        return {hid: (True, [{"ok": True}], None) for hid in host_ids}

    with patch(
        "backend.services.precheck.verify.gather_verify", new=fake_gather,
    ):
        asyncio.run(
            plan_admission_task(
                {}, plan_run_id=plan_run_id, attempt_id=attempt,
            ),
        )
    db_session.expire_all()


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
    def test_parent_success_triggers_queued_child_plan_run(
        self, db_session, gate_chain,
    ):
        parent_plan, child_plan = _seed_chain_plans(db_session, gate_chain)

        parent_run = dispatch_plan_sync(
            plan_id=parent_plan.id,
            device_ids=[gate_chain["device_a"].id],
            triggered_by="integration-test",
            db=db_session,
            run_type="MANUAL",
        )
        assert parent_run.status == "QUEUED"
        _admit_plan_run(db_session, parent_run.id)
        _complete_parent_and_aggregate(db_session, parent_run)

        child_run = trigger_next_plan_sync(
            db_session.get(PlanRun, parent_run.id), db_session,
        )

        db_session.expire_all()
        parent_refreshed = db_session.get(PlanRun, parent_run.id)
        assert parent_refreshed.status == "SUCCESS"
        assert parent_refreshed.next_plan_triggered is True

        assert child_run is not None
        assert child_run.plan_id == child_plan.id
        assert child_run.run_type == "CHAIN"
        assert child_run.status == "QUEUED"
        assert (
            db_session.query(JobInstance)
            .filter(JobInstance.plan_run_id == child_run.id)
            .count()
            == 0
        )

    def test_chain_dispatch_failure_rolls_back_next_plan_triggered(
        self, db_session, gate_chain,
    ):
        parent_plan, _child_plan = _seed_chain_plans(db_session, gate_chain)

        parent_run = dispatch_plan_sync(
            plan_id=parent_plan.id,
            device_ids=[gate_chain["device_a"].id],
            triggered_by="integration-test",
            db=db_session,
            run_type="MANUAL",
        )
        _admit_plan_run(db_session, parent_run.id)

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
