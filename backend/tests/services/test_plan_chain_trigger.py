"""Plan chain trigger — dispatch failure rollback."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from backend.models.enums import JobStatus
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services.plan_chain_trigger import trigger_next_plan_sync
from backend.services.plan_dispatcher_core import PlanDispatchError


def _seed_successful_parent_run(db_session, sample_device, sample_host):
    child_plan = Plan(name="chain-child", failure_threshold=0.1)
    parent_plan = Plan(name="chain-parent", failure_threshold=0.1, next_plan_id=None)
    db_session.add_all([parent_plan, child_plan])
    db_session.flush()
    parent_plan.next_plan_id = child_plan.id

    pr = PlanRun(
        plan_id=parent_plan.id,
        status="SUCCESS",
        failure_threshold=0.1,
        plan_snapshot={"plan_id": parent_plan.id},
        run_type="MANUAL",
        triggered_by="test",
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(pr)
    db_session.flush()

    job = JobInstance(
        plan_run_id=pr.id,
        plan_id=parent_plan.id,
        device_id=sample_device.id,
        host_id=sample_host.id,
        status=JobStatus.COMPLETED.value,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(pr)
    return pr


class TestPlanChainTriggerRollback:
    def test_dispatch_failure_rolls_back_next_plan_triggered(
        self, db_session, sample_device, sample_host,
    ):
        pr = _seed_successful_parent_run(db_session, sample_device, sample_host)

        with patch(
            "backend.services.plan_chain_trigger.dispatch_plan_sync",
            side_effect=PlanDispatchError("devices unavailable"),
        ):
            result = trigger_next_plan_sync(pr, db_session)

        assert result is None
        db_session.expire_all()
        refreshed = db_session.get(PlanRun, pr.id)
        assert refreshed.next_plan_triggered is False
        assert refreshed.result_summary is not None
        assert "chain_dispatch_failed" in refreshed.result_summary
        assert "devices unavailable" in refreshed.result_summary["chain_dispatch_failed"]["error"]
