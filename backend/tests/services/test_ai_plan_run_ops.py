"""AI 助手 PlanRun 运维写操作（PR-C）单测。"""

import pytest

from backend.services.ai_assistant.plan_run_ops import (
    describe_abort_preview,
    normalize_abort_params,
    normalize_archive_params,
    normalize_manual_job_params,
    normalize_retry_dispatch_params,
    run_abort_plan_run,
    run_manual_retry_job,
    run_trigger_plan_run_archive,
)
from backend.services.ai_assistant.tools import ToolValidationError


class TestNormalizePlanRunOpsParams:
    def test_abort_requires_positive_run_id(self):
        with pytest.raises(ToolValidationError):
            normalize_abort_params({"run_id": 0})
        out = normalize_abort_params({"run_id": 9, "reason": "  stop  "})
        assert out == {"run_id": 9, "reason": "stop"}

    def test_manual_job_requires_both_ids(self):
        with pytest.raises(ToolValidationError):
            normalize_manual_job_params({"run_id": 1}, default_reason="x")
        out = normalize_manual_job_params(
            {"run_id": 1, "job_id": 2},
            default_reason="manual_retry",
        )
        assert out["run_id"] == 1 and out["job_id"] == 2

    def test_archive_and_retry_dispatch(self):
        assert normalize_retry_dispatch_params({"run_id": 3}) == {"run_id": 3}
        assert normalize_archive_params({"run_id": 4}) == {"run_id": 4}


class TestRunAbortPlanRun:
    def test_delegates_to_abort_service(self, db_session, monkeypatch, test_user):
        captured: dict = {}

        def _fake_abort(run_id, *, db, reason, triggered_by, audit_user_id, audit_username, audit_action):
            captured.update({
                "run_id": run_id,
                "reason": reason,
                "audit_action": audit_action,
                "user_id": audit_user_id,
            })
            return {
                "plan_run_id": run_id,
                "status": "FAILED",
                "aborted_jobs": [1],
                "phase": "running",
            }

        monkeypatch.setattr(
            "backend.services.ai_assistant.plan_run_ops.abort_plan_run",
            _fake_abort,
        )

        summary = run_abort_plan_run(
            db_session,
            {"run_id": 42, "reason": "ops stop"},
            triggered_by=test_user.username,
            requester_user_id=test_user.id,
        )
        assert "PlanRun #42" in summary
        assert captured["audit_action"] == "ai_assistant_abort_plan_run"
        assert captured["user_id"] == test_user.id


class TestRunManualRetryJob:
    def test_sets_retry_now_on_running_job(
        self, db_session, sample_running_job, test_user,
    ):
        job = sample_running_job
        from backend.models.host import Device

        dev = db_session.get(Device, job.device_id)
        dev.adb_connected = True
        dev.status = "ONLINE"
        db_session.commit()

        summary = run_manual_retry_job(
            db_session,
            {"run_id": job.plan_run_id, "job_id": job.id, "reason": "stuck"},
            triggered_by=test_user.username,
            requester_user_id=test_user.id,
        )
        assert "RETRY_NOW" in summary
        db_session.refresh(job)
        assert job.manual_action == "RETRY_NOW"
        assert job.current_failure_streak == 0


class TestRunTriggerArchive:
    def test_records_audit_when_hosts_online(
        self, db_session, sample_job_instance, monkeypatch, test_user,
    ):
        job = sample_job_instance
        job.status = "COMPLETED"
        db_session.commit()
        pr_id = job.plan_run_id

        emitted: list[tuple] = []
        monkeypatch.setattr(
            "backend.services.ai_assistant.plan_run_ops._schedule_emit_agent_control",
            lambda host_id, command, *, payload=None: emitted.append((host_id, command)),
        )

        summary = run_trigger_plan_run_archive(
            db_session,
            {"run_id": pr_id},
            triggered_by=test_user.username,
            requester_user_id=test_user.id,
        )
        assert "归档/扫描已触发" in summary
        assert any(cmd == "archive_now" for _, cmd in emitted)
        assert any(cmd == "scan_now" for _, cmd in emitted)

        from backend.models.audit import AuditLog

        row = (
            db_session.query(AuditLog)
            .filter(AuditLog.action == "ai_assistant_trigger_plan_run_archive")
            .first()
        )
        assert row is not None
        assert int(row.resource_id) == pr_id


class TestDescribeAbortPreview:
    def test_includes_status_and_job_counts(self, db_session, sample_plan_run):
        text = describe_abort_preview(
            db_session,
            {"run_id": sample_plan_run.id, "reason": "test"},
        )
        assert "RUNNING" in text
        assert "test-plan" in text
