"""#1076 — post_processed_at must not commit before case ingest succeeds."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from backend.models.job import JobInstance, StepTrace
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.case_result import TestCaseResult
from backend.services.post_completion import run_post_completion


def _seed_job(db_session, sample_device):
    plan = Plan(name="pc-plan")
    db_session.add(plan)
    db_session.flush()
    pr = PlanRun(
        plan_id=plan.id,
        status="SUCCESS",
        run_type="MANUAL",
        plan_snapshot={},
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(pr)
    db_session.flush()
    job = JobInstance(
        plan_id=plan.id,
        plan_run_id=pr.id,
        device_id=sample_device.id,
        host_id=sample_device.host_id,
        status="COMPLETED",
        pipeline_def={"lifecycle": {}},
    )
    db_session.add(job)
    db_session.flush()
    return job


def _fake_report():
    return SimpleNamespace(
        model_dump=lambda mode="json": {"risk_summary": None},
        risk_summary=None,
    )


def test_post_completion_defers_when_detail_file_missing(
    db_session, sample_device, monkeypatch,
):
    job = _seed_job(db_session, sample_device)
    missing = "/tmp/does-not-exist-mtbf-detail.json"
    db_session.add(StepTrace(
        job_id=job.id,
        step_id="finish",
        stage="teardown",
        event_type="COMPLETED",
        status="COMPLETED",
        output=json.dumps({"success": True, "detail_uri": missing}),
        original_ts=datetime.now(timezone.utc),
    ))
    db_session.commit()

    monkeypatch.setattr(
        "backend.services.report_service.compose_run_report",
        lambda db, job_id: _fake_report(),
    )
    monkeypatch.setattr(
        "backend.services.report_service.build_jira_draft",
        lambda report: SimpleNamespace(model_dump=lambda mode="json": {}),
    )
    monkeypatch.setattr(
        "backend.services.plan_chain_trigger.reconcile_chain_trigger_sync",
        lambda plan_run_id, db: None,
    )

    assert run_post_completion(job.id, db_session) is False
    db_session.refresh(job)
    assert job.post_processed_at is None
    assert job.report_json is None


def test_post_completion_succeeds_after_late_detail_file(
    db_session, sample_device, monkeypatch, tmp_path,
):
    job = _seed_job(db_session, sample_device)
    detail = tmp_path / "late.json"
    db_session.add(StepTrace(
        job_id=job.id,
        step_id="finish",
        stage="teardown",
        event_type="COMPLETED",
        status="COMPLETED",
        output=json.dumps({"success": True, "detail_uri": str(detail)}),
        original_ts=datetime.now(timezone.utc),
    ))
    db_session.commit()

    monkeypatch.setattr(
        "backend.services.report_service.compose_run_report",
        lambda db, job_id: _fake_report(),
    )
    monkeypatch.setattr(
        "backend.services.report_service.build_jira_draft",
        lambda report: SimpleNamespace(model_dump=lambda mode="json": {}),
    )
    monkeypatch.setattr(
        "backend.services.plan_chain_trigger.reconcile_chain_trigger_sync",
        lambda plan_run_id, db: None,
    )

    assert run_post_completion(job.id, db_session) is False

    detail.write_text(json.dumps({
        "testpoints": [{"name": "case_a", "status": "PASS", "testcases": []}],
    }), encoding="utf-8")
    assert run_post_completion(job.id, db_session) is True

    db_session.refresh(job)
    assert job.post_processed_at is not None
    assert db_session.query(TestCaseResult).filter_by(job_id=job.id).count() == 1


def test_post_completion_ingest_failure_leaves_post_processed_null(
    db_session, sample_device, monkeypatch, tmp_path,
):
    job = _seed_job(db_session, sample_device)
    detail = tmp_path / "ok.json"
    detail.write_text(json.dumps({
        "testpoints": [{"name": "case_a", "status": "PASS", "testcases": []}],
    }), encoding="utf-8")
    db_session.add(StepTrace(
        job_id=job.id,
        step_id="finish",
        stage="teardown",
        event_type="COMPLETED",
        status="COMPLETED",
        output=json.dumps({"success": True, "detail_uri": str(detail)}),
        original_ts=datetime.now(timezone.utc),
    ))
    db_session.commit()

    monkeypatch.setattr(
        "backend.services.report_service.compose_run_report",
        lambda db, job_id: _fake_report(),
    )
    monkeypatch.setattr(
        "backend.services.report_service.build_jira_draft",
        lambda report: SimpleNamespace(model_dump=lambda mode="json": {}),
    )
    monkeypatch.setattr(
        "backend.services.plan_chain_trigger.reconcile_chain_trigger_sync",
        lambda plan_run_id, db: None,
    )
    monkeypatch.setattr(
        "backend.services.case_result_ingest.ingest_test_case_results_for_job",
        MagicMock(side_effect=RuntimeError("db write failed")),
    )

    assert run_post_completion(job.id, db_session) is False
    db_session.refresh(job)
    assert job.post_processed_at is None
