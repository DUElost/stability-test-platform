"""Plan dispatcher unit tests — ADR-0020."""

import pytest

from backend.models.enums import HostStatus
from backend.models.host import Device, Host
from backend.models.plan import Plan, PlanStep
from backend.models.script import Script
from backend.services.plan_dispatcher_sync import (
    _build_lifecycle_from_steps,
    _build_preview,
    PlanDispatchError,
    dispatch_plan_sync,
    preview_plan_dispatch_sync,
)


# ── Pure-unit tests ─────────────────────────────────────────────────────

class TestBuildLifecycle:
    def test_init_and_teardown(self):
        plan = Plan(name="p", lifecycle={})
        steps = [
            PlanStep(plan_id=1, step_key="s1", script_name="init_s",
                     script_version="1.0.0", stage="init", sort_order=0),
            PlanStep(plan_id=1, step_key="s2", script_name="td_s",
                     script_version="1.0.0", stage="teardown", sort_order=0),
        ]
        defaults = {("init_s", "1.0.0"): {"x": 1},
                    ("td_s", "1.0.0"): {"y": 2}}
        lc = _build_lifecycle_from_steps(plan, steps, defaults)
        assert len(lc["init"]) == 1
        assert lc["init"][0]["params"] == {"x": 1}
        assert lc["init"][0]["action"] == "script:init_s"
        assert len(lc["teardown"]) == 1
        assert lc["teardown"][0]["params"] == {"y": 2}

    def test_patrol_steps(self):
        plan = Plan(name="p", lifecycle={"patrol": {"interval_seconds": 30}})
        steps = [
            PlanStep(plan_id=1, step_key="p1", script_name="patrol_s",
                     script_version="1.0.0", stage="patrol", sort_order=0),
        ]
        defaults = {("patrol_s", "1.0.0"): {}}
        lc = _build_lifecycle_from_steps(plan, steps, defaults)
        assert "patrol" in lc
        assert lc["patrol"]["interval_seconds"] == 30
        assert len(lc["patrol"]["steps"]) == 1

    def test_plan_timeout(self):
        plan = Plan(name="p", lifecycle={"timeout_seconds": 900})
        steps = [
            PlanStep(plan_id=1, step_key="s1", script_name="a",
                     script_version="1.0.0", stage="init", sort_order=0),
        ]
        defaults = {("a", "1.0.0"): {}}
        lc = _build_lifecycle_from_steps(plan, steps, defaults)
        assert lc["timeout_seconds"] == 900

    def test_sort_order_ordering(self):
        plan = Plan(name="p", lifecycle={})
        steps = [
            PlanStep(plan_id=1, step_key="s3", script_name="a",
                     script_version="1.0.0", stage="init", sort_order=2),
            PlanStep(plan_id=1, step_key="s1", script_name="a",
                     script_version="1.0.0", stage="init", sort_order=0),
            PlanStep(plan_id=1, step_key="s2", script_name="a",
                     script_version="1.0.0", stage="init", sort_order=1),
        ]
        defaults = {("a", "1.0.0"): {}}
        lc = _build_lifecycle_from_steps(plan, steps, defaults)
        keys = [s["step_id"] for s in lc["init"]]
        assert keys == ["s1", "s2", "s3"]


class TestBuildPreview:
    def test_preview_structure(self):
        plan = Plan(id=5, name="preview-plan")
        lifecycle = {"init": [{"step_id": "a"}], "teardown": []}
        preview = _build_preview(plan, lifecycle, [10, 20])
        assert preview["plan_id"] == 5
        assert preview["plan_name"] == "preview-plan"
        assert preview["device_count"] == 2
        assert preview["job_count"] == 2
        assert preview["total_steps"] == 1


# ── Integration tests ───────────────────────────────────────────────────

@pytest.fixture
def _plan_fixture(db_session):
    """Create a minimal Plan + PlanStep + Script + Device + Host for dispatch."""
    host = Host(id="h-disp", hostname="hdisp",
                status=HostStatus.ONLINE.value)
    device = Device(serial="S-disp", host_id="h-disp", status="ONLINE")
    script = Script(
        name="check_device", script_type="python", version="1.0.0",
        nfs_path="/s/check_device.py", content_sha256="abc",
        default_params={"timeout": 30},
    )
    plan = Plan(name="dispatch-test", lifecycle={"init": [], "teardown": []})
    db_session.add_all([host, device, script, plan])
    db_session.commit()

    step = PlanStep(
        plan_id=plan.id, step_key="init_check",
        script_name="check_device", script_version="1.0.0",
        stage="init", sort_order=0, timeout_seconds=30, retry=0,
    )
    teardown_step = PlanStep(
        plan_id=plan.id, step_key="td_clean",
        script_name="check_device", script_version="1.0.0",
        stage="teardown", sort_order=0, timeout_seconds=10, retry=0,
    )
    db_session.add_all([step, teardown_step])
    db_session.commit()
    return plan, device, host


class TestDispatchPlan:
    def test_dispatch_creates_plan_run_and_jobs(self, db_session, _plan_fixture):
        plan, device, host = _plan_fixture

        pr = dispatch_plan_sync(
            plan_id=plan.id,
            device_ids=[device.id],
            triggered_by="test",
            db=db_session,
        )

        assert pr.id is not None
        assert pr.plan_id == plan.id
        assert pr.status == "RUNNING"
        assert pr.run_type == "MANUAL"

        from backend.models.job import JobInstance
        jobs = db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id
        ).all()
        assert len(jobs) == 1
        assert jobs[0].device_id == device.id
        assert jobs[0].plan_id == plan.id
        assert jobs[0].status == "PENDING"

    def test_preview_returns_structure(self, db_session, _plan_fixture):
        plan, device, host = _plan_fixture

        preview = preview_plan_dispatch_sync(
            plan_id=plan.id,
            device_ids=[device.id],
            db=db_session,
        )

        assert preview["plan_id"] == plan.id
        assert preview["plan_name"] == plan.name
        assert preview["device_count"] == 1
        assert preview["job_count"] == 1
        assert "lifecycle" in preview


class TestValidation:
    def test_missing_plan_raises(self, db_session):
        with pytest.raises(PlanDispatchError, match="not found"):
            dispatch_plan_sync(
                plan_id=99999,
                device_ids=[1],
                triggered_by="test",
                db=db_session,
            )
