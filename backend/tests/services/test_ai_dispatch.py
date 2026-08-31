"""AI 助手 PlanRun 派发（PR-B）单测。"""

import pytest

from backend.services.ai_assistant.dispatch import (
    describe_dispatch_preview,
    normalize_dispatch_params,
    run_dispatch_plan_run,
)
from backend.services.ai_assistant.tools import ToolValidationError


class TestNormalizeDispatchParams:
    def test_requires_unique_positive_device_ids(self):
        with pytest.raises(ToolValidationError):
            normalize_dispatch_params({"plan_id": 1, "device_ids": [1, 1]})
        with pytest.raises(ToolValidationError):
            normalize_dispatch_params({"plan_id": 1, "device_ids": []})

    def test_normalizes_note_and_wifi_pool(self):
        out = normalize_dispatch_params({
            "plan_id": 5,
            "device_ids": [10, 11],
            "note": "  gpu batch  ",
            "wifi_pool_id": 3,
        })
        assert out == {
            "plan_id": 5,
            "device_ids": [10, 11],
            "note": "gpu batch",
            "wifi_pool_id": 3,
        }


class TestRunDispatchPlanRun:
    def test_records_audit_and_returns_summary(self, db_session, monkeypatch, test_user):
        from backend.models.plan import Plan

        plan = Plan(name="gpu-test", failure_threshold=0.05)
        db_session.add(plan)
        db_session.commit()

        def _fake_execute(db, params, *, triggered_by):
            return 99, "PlanRun #99 已入队（status=QUEUED）"

        monkeypatch.setattr(
            "backend.services.ai_assistant.dispatch.execute_dispatch_plan_run",
            _fake_execute,
        )

        summary = run_dispatch_plan_run(
            db_session,
            {"plan_id": plan.id, "device_ids": [1]},
            triggered_by=test_user.username,
            requester_user_id=test_user.id,
        )
        assert "PlanRun #99" in summary

        from backend.models.audit import AuditLog

        row = (
            db_session.query(AuditLog)
            .filter(AuditLog.action == "ai_assistant_dispatch_plan_run")
            .first()
        )
        assert row is not None
        assert int(row.resource_id) == 99


class TestDescribeDispatchPreview:
    def test_includes_plan_and_devices(self, db_session):
        from backend.models.host import Device, Host
        from backend.models.plan import Plan

        plan = Plan(name="p1", failure_threshold=0.1)
        db_session.add(plan)
        db_session.flush()
        host = Host(id="h1", hostname="host1", status="ONLINE")
        db_session.add(host)
        dev = Device(serial="SN001", host_id="h1", status="ONLINE")
        db_session.add(dev)
        db_session.commit()

        text = describe_dispatch_preview(
            db_session,
            {"plan_id": plan.id, "device_ids": [dev.id]},
        )
        assert "p1" in text
        assert "SN001" in text
