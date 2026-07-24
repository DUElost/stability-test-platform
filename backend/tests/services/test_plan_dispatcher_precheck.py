"""Admission precheck integration tests."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun
from backend.services.admission_pump import (
    claim_queued_plan_runs,
    plan_admission_task,
)
from backend.services.plan_dispatcher_sync import prepare_plan_run
from backend.tests.services.precheck_helpers import ack_ok


class TestSingleDeviceDispatchPrecheck:
    def test_single_device_admission_precheck_pass(
        self, db_session, single_device_gate_chain
    ):
        chain = single_device_gate_chain
        pr = prepare_plan_run(
            plan_id=chain["plan"].id,
            device_ids=[chain["device"].id],
            triggered_by="testuser",
            db=db_session,
            run_type="MANUAL",
        )

        async def _fake_call(host_id, event, data, *, timeout=10.0):
            return ack_ok(host_id, "aabbcc11")

        with patch(
            "backend.services.precheck.verify.call_agent_rpc",
            side_effect=_fake_call,
        ):
            claimed = claim_queued_plan_runs(db_session)
            assert len(claimed) == 1
            assert claimed[0][0] == pr.id
            asyncio.run(plan_admission_task(
                {}, plan_run_id=pr.id, attempt_id=claimed[0][1],
            ))

        db_session.expire_all()
        pr_after: PlanRun = db_session.get(PlanRun, pr.id)
        assert pr_after.status == "RUNNING"
        assert pr_after.run_context["dispatch_state"]["status"] == "completed"

        jobs = (
            db_session.query(JobInstance)
            .filter(JobInstance.plan_run_id == pr.id)
            .all()
        )
        assert len(jobs) == 1
        assert jobs[0].device_id == chain["device"].id
