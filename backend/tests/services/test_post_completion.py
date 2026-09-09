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


def test_post_completion_succeeds_when_detail_has_empty_testpoints(
    db_session, sample_device, monkeypatch, tmp_path,
):
    """#1175: detail 文件已存在且为合法 dict、testpoints 为空列表 = 终态
    （合法零用例），不得永久当 pending 回滚——否则报告永失且无限重算。"""
    job = _seed_job(db_session, sample_device)
    detail = tmp_path / "empty.json"
    detail.write_text(json.dumps({"testpoints": []}), encoding="utf-8")
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

    assert run_post_completion(job.id, db_session) is True
    db_session.refresh(job)
    assert job.post_processed_at is not None
    assert job.report_json == {"risk_summary": None}
    assert db_session.query(TestCaseResult).filter_by(job_id=job.id).count() == 0


def test_post_completion_succeeds_when_detail_lacks_testpoints_key(
    db_session, sample_device, monkeypatch, tmp_path,
):
    """#1175: metrics-only detail（无 testpoints 键）同属终态 dict，放行提交。"""
    job = _seed_job(db_session, sample_device)
    detail = tmp_path / "metrics-only.json"
    detail.write_text(json.dumps({
        "metrics": {"run_dir": "/nfs/run/1", "total": 0},
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

    assert run_post_completion(job.id, db_session) is True
    db_session.refresh(job)
    assert job.post_processed_at is not None


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
