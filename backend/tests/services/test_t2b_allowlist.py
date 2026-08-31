"""T2b 自动派发白名单单测。"""

from types import SimpleNamespace

from backend.services.ai_assistant.orchestrator import _decide_execution_mode
from backend.services.ai_assistant.t2b_allowlist import (
    dispatch_matches_t2b_allowlist,
    sanitize_t2b_auto_dispatch_allowlist,
)
from backend.services.ai_assistant.tools import TOOLS


class TestSanitizeAllowlist:
    def test_drops_invalid_and_missing_plan(self, db_session):
        from backend.models.plan import Plan

        plan = Plan(name="active", failure_threshold=0.1)
        db_session.add(plan)
        db_session.commit()

        cleaned, dropped = sanitize_t2b_auto_dispatch_allowlist(
            [
                {"plan_id": plan.id, "max_devices": 3},
                {"plan_id": 99999, "max_devices": 1},
                "bad",
            ],
            db_session,
        )
        assert len(cleaned) == 1
        assert cleaned[0]["plan_id"] == plan.id
        assert cleaned[0]["max_devices"] == 3
        assert len(dropped) == 2


class TestDispatchMatchesAllowlist:
    def test_matches_when_within_device_cap(self, db_session):
        from backend.models.plan import Plan

        plan = Plan(name="gpu", failure_threshold=0.1)
        db_session.add(plan)
        db_session.commit()
        cfg = SimpleNamespace(
            t2b_auto_dispatch_allowlist=[
                {"plan_id": plan.id, "max_devices": 3, "tools": ["dispatch_plan_run"]},
            ],
        )
        assert dispatch_matches_t2b_allowlist(
            cfg,
            {"plan_id": plan.id, "device_ids": [1, 2]},
            db_session,
        )
        assert not dispatch_matches_t2b_allowlist(
            cfg,
            {"plan_id": plan.id, "device_ids": [1, 2, 3, 4]},
            db_session,
        )


class TestDecideExecutionMode:
    def test_dispatch_auto_when_allowlisted(self, db_session, test_user):
        from backend.models.plan import Plan

        plan = Plan(name="gpu", failure_threshold=0.1)
        db_session.add(plan)
        db_session.commit()
        cfg = SimpleNamespace(
            t1_require_confirm=False,
            auto_approve_tools=[],
            t2b_auto_dispatch_allowlist=[
                {"plan_id": plan.id, "max_devices": 5, "tools": ["dispatch_plan_run"]},
            ],
        )
        spec = TOOLS["dispatch_plan_run"]
        mode = _decide_execution_mode(
            spec,
            cfg,
            test_user,
            tool_args={"plan_id": plan.id, "device_ids": [1]},
            db=db_session,
        )
        assert mode == "auto"

    def test_dispatch_stays_proposed_without_allowlist(self, db_session, test_user):
        from backend.models.plan import Plan

        plan = Plan(name="gpu", failure_threshold=0.1)
        db_session.add(plan)
        db_session.commit()
        cfg = SimpleNamespace(
            t1_require_confirm=False,
            auto_approve_tools=[],
            t2b_auto_dispatch_allowlist=[],
        )
        spec = TOOLS["dispatch_plan_run"]
        mode = _decide_execution_mode(
            spec,
            cfg,
            test_user,
            tool_args={"plan_id": plan.id, "device_ids": [1]},
            db=db_session,
        )
        assert mode == "proposed"

    def test_allowlist_does_not_bypass_admin_only(self, db_session):
        cfg = SimpleNamespace(
            t1_require_confirm=False,
            auto_approve_tools=[],
            t2b_auto_dispatch_allowlist=[],
        )
        spec = TOOLS["test_notification_channel"]
        mode = _decide_execution_mode(
            spec,
            cfg,
            SimpleNamespace(role="user"),
            tool_args={"channel_id": 1},
            db=db_session,
        )
        assert mode == "proposed"
