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


# ── #1082：报告缓存语义 —— 快照 + 终态刷新 ────────────────────────────────


def test_refresh_report_cache_for_plan_run_recomputes(db_session, sample_device, monkeypatch):
    """PlanRun 终态刷新：重算全部已后处理 job 的缓存并更新生成时刻。"""
    from backend.services.post_completion import refresh_report_cache_for_plan_run

    plan = Plan(name="rc-plan")
    db_session.add(plan)
    db_session.flush()
    pr = PlanRun(
        plan_id=plan.id, status="RUNNING", run_type="MANUAL",
        plan_snapshot={}, started_at=datetime.now(timezone.utc),
    )
    db_session.add(pr)
    db_session.flush()
    from backend.models.enums import DeviceStatus
    from backend.models.host import Device

    devices = []
    for i in range(3):
        dev = Device(
            serial=f"rc-dev-{i}", host_id=sample_device.host_id,
            status=DeviceStatus.ONLINE.value,
            last_seen=datetime.now(timezone.utc), adb_connected=True,
            adb_state="device", battery_level=80, temperature=35,
        )
        db_session.add(dev)
        devices.append(dev)
    db_session.flush()

    jobs = []
    for dev, processed in ((devices[0], True), (devices[1], True), (devices[2], False)):
        job = JobInstance(
            plan_id=plan.id, plan_run_id=pr.id,
            device_id=dev.id, host_id=sample_device.host_id,
            status="COMPLETED", pipeline_def={"lifecycle": {}},
        )
        if processed:
            job.report_json = {"risk_summary": None, "stale": True}
            job.post_processed_at = datetime.now(timezone.utc)
        db_session.add(job)
        if processed:
            jobs.append(job)
    db_session.commit()
    old_ts = {j.id: j.post_processed_at for j in jobs}

    calls = []

    def fake_compose(db, job_id):
        calls.append(job_id)
        return SimpleNamespace(model_dump=lambda mode="json": {
            "risk_summary": {"risk_level": "S"},
        })

    monkeypatch.setattr(
        "backend.services.report_service.compose_run_report", fake_compose,
    )

    refreshed = refresh_report_cache_for_plan_run(pr.id)

    assert refreshed == 2
    assert sorted(calls) == sorted(j.id for j in jobs)
    for j in jobs:
        db_session.expire(j)
        assert j.report_json["risk_summary"]["risk_level"] == "S"
        assert "stale" not in j.report_json
        assert j.post_processed_at > old_ts[j.id]


def test_cached_report_carries_cached_at(db_session, sample_device):
    """缓存命中时响应带 cached_at（= post_processed_at），供 UI 标注快照时刻。"""
    from backend.api.routes.runs import get_cached_run_report

    plan = Plan(name="rc-plan2")
    db_session.add(plan)
    db_session.flush()
    pr = PlanRun(
        plan_id=plan.id, status="RUNNING", run_type="MANUAL",
        plan_snapshot={}, started_at=datetime.now(timezone.utc),
    )
    db_session.add(pr)
    db_session.flush()
    job = JobInstance(
        plan_id=plan.id, plan_run_id=pr.id,
        device_id=sample_device.id, host_id=sample_device.host_id,
        status="COMPLETED", pipeline_def={"lifecycle": {}},
        report_json={"risk_summary": {"risk_level": "A"}},
        post_processed_at=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc),
    )
    db_session.add(job)
    db_session.commit()

    r = get_cached_run_report(run_id=job.id, db=db_session, _current_user=None)
    assert r.data["risk_summary"]["risk_level"] == "A"
    assert r.data["cached_at"] == "2026-09-09T12:00:00+00:00"


def test_finalize_schedules_refresh_outside_testing(monkeypatch):
    """调度门控：TESTING=1（pytest）跳过；非测试环境经线程池提交。"""
    from backend.services import post_completion

    submitted: list = []

    def fake_submit(fn, *args, **kwargs):
        submitted.append((fn, args))

    monkeypatch.setattr(
        "backend.core.thread_pool.submit", fake_submit,
    )
    # 非 TESTING：提交刷新任务
    monkeypatch.delenv("TESTING", raising=False)
    post_completion._schedule_report_cache_refresh(77)
    assert len(submitted) == 1
    fn, args = submitted[0]
    assert fn is post_completion.refresh_report_cache_for_plan_run
    assert args == (77,)

    # TESTING=1：跳过（避免后台线程与 TRUNCATE 隔离竞争）
    monkeypatch.setenv("TESTING", "1")
    post_completion._schedule_report_cache_refresh(78)
    assert len(submitted) == 1


def test_finalize_plan_run_triggers_schedule(monkeypatch):
    """_finalize_plan_run 收口处调用刷新调度（TESTING 下为 no-op，但调用必达）。"""
    from types import SimpleNamespace as NS

    from backend.models.enums import JobStatus, PlanRunStatus
    from backend.services import plan_run_aggregation

    called: list = []
    monkeypatch.setattr(
        plan_run_aggregation,
        "_notify_plan_run_terminal",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "backend.services.post_completion._schedule_report_cache_refresh",
        lambda plan_run_id: called.append(plan_run_id),
    )

    run = NS(
        id=55, status=PlanRunStatus.RUNNING.value,
        failure_threshold=0.5, ended_at=None, result_summary=None,
        run_context=None,
    )
    applied = plan_run_aggregation.apply_plan_run_aggregation(
        run,
        [NS(status=JobStatus.COMPLETED.value), NS(status=JobStatus.COMPLETED.value)],
    )
    assert applied is True
    assert called == [55]
