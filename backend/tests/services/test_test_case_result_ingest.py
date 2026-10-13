"""test_case_result 摄入与查询。"""

import json
from datetime import datetime, timezone

import pytest

from backend.models.job import JobInstance, StepTrace
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.suite import TestCase, TestSuite
from backend.services.case_result_ingest import (
    ingest_test_case_results_for_job,
    list_plan_run_test_case_results,
)


@pytest.fixture
def mtbf_fixture(db_session, sample_device):
    suite = TestSuite(name="mtbf_smoke", is_active=True)
    db_session.add(suite)
    db_session.flush()
    db_session.add(TestCase(
        suite_id=suite.id,
        name="case_a",
        ordinal=1,
        times=1,
        enabled=True,
        exec_descs=[],
    ))
    plan = Plan(name="mtbf-plan", suite_id=suite.id)
    db_session.add(plan)
    db_session.flush()
    pr = PlanRun(
        plan_id=plan.id,
        status="SUCCESS",
        run_type="MANUAL",
        plan_snapshot={},
        run_context={"dispatch_suite": {"suite_id": suite.id, "export_dir": "legacy"}},
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
    return {"suite": suite, "plan": plan, "plan_run": pr, "job": job}


def test_ingest_from_detail_json(db_session, mtbf_fixture, tmp_path):
    job = mtbf_fixture["job"]
    detail = tmp_path / "legacy_run.json"
    detail.write_text(json.dumps({
        "run_dir": "20260831_120000",
        "metrics": {"passed": 1, "failed": 1},
        "testpoints": [
            {"name": "case_a", "status": "PASS", "testcases": [{"status": "PASS"}]},
            {
                "name": "case_b",
                "status": "FAILURE",
                "testcases": [{"status": "FAILURE", "message": "boom"}],
            },
        ],
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

    count = ingest_test_case_results_for_job(db_session, job.id)
    db_session.commit()
    assert count == 2

    rows, total, summary = list_plan_run_test_case_results(
        db_session, mtbf_fixture["plan_run"].id,
    )
    assert total == 2
    assert summary["passed"] == 1
    assert summary["failed"] == 1
    by_name = {r.case_name: r for r in rows}
    case_a = db_session.query(TestCase).filter_by(suite_id=mtbf_fixture["suite"].id, name="case_a").one()
    assert by_name["case_a"].case_id == case_a.id
    assert by_name["case_a"].status == "PASS"
    assert by_name["case_b"].detail == "boom"

    # 幂等：第二次摄入为 0
    assert ingest_test_case_results_for_job(db_session, job.id) == 0


def test_ingest_skips_without_detail_uri(db_session, mtbf_fixture):
    job = mtbf_fixture["job"]
    db_session.add(StepTrace(
        job_id=job.id,
        step_id="finish",
        stage="teardown",
        event_type="COMPLETED",
        status="COMPLETED",
        output=json.dumps({"success": True}),
        original_ts=datetime.now(timezone.utc),
    ))
    db_session.commit()
    assert ingest_test_case_results_for_job(db_session, job.id) == 0


def _attach_detail(db_session, job, tmp_path, payload, *, name="detail.json"):
    detail = tmp_path / name
    detail.write_text(json.dumps(payload), encoding="utf-8")
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
    return detail


def test_ingest_merges_duplicate_failure_names_3700(db_session, mtbf_fixture, tmp_path):
    """A1: 同名两条 FAILURE 合并为 1 行 FAILURE，detail 含「执行 2 次」。"""
    job = mtbf_fixture["job"]
    _attach_detail(db_session, job, tmp_path, {
        "run_dir": "synthetic_run",
        "testpoints": [
            {
                "name": "dup_case",
                "status": "FAILURE",
                "testcases": [{"status": "FAILURE", "message": "first boom"}],
            },
            {
                "name": "dup_case",
                "status": "FAILURE",
                "testcases": [{"status": "FAILURE", "message": "second boom"}],
            },
        ],
    })

    count = ingest_test_case_results_for_job(db_session, job.id)
    db_session.commit()
    assert count == 1

    rows, total, summary = list_plan_run_test_case_results(
        db_session, mtbf_fixture["plan_run"].id,
    )
    assert total == 1
    assert summary["failed"] == 1
    row = rows[0]
    assert row.case_name == "dup_case"
    assert row.status == "FAILURE"
    assert "执行 2 次" in (row.detail or "")
    assert "FAILURE → FAILURE" in (row.detail or "")
    assert "second boom" in (row.detail or "")


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (("FAILURE", "PASS"), "FAILURE"),
        (("PASS", "FAILURE"), "FAILURE"),
        (("PASS", "PASS"), "PASS"),
    ],
    ids=["failure_then_pass", "pass_then_failure", "pass_pass"],
)
def test_ingest_merge_status_rules_3700(
    db_session, mtbf_fixture, tmp_path, statuses, expected,
):
    """A2: 任一次非 PASS → 取最后一次非 PASS；全 PASS → PASS。"""
    job = mtbf_fixture["job"]
    testpoints = []
    for i, st in enumerate(statuses):
        tc = {"status": st}
        if st != "PASS":
            tc["message"] = f"msg-{i}"
        testpoints.append({"name": "status_case", "status": st, "testcases": [tc]})
    _attach_detail(
        db_session, job, tmp_path,
        {"run_dir": "synthetic_status", "testpoints": testpoints},
        name=f"status_{'_'.join(statuses)}.json",
    )

    count = ingest_test_case_results_for_job(db_session, job.id)
    db_session.commit()
    assert count == 1
    rows, total, _summary = list_plan_run_test_case_results(
        db_session, mtbf_fixture["plan_run"].id,
    )
    assert total == 1
    assert rows[0].status == expected
    assert f"执行 {len(statuses)} 次" in (rows[0].detail or "")


def test_ingest_no_duplicate_unchanged_3700(db_session, mtbf_fixture, tmp_path):
    """A3: 无重名明细的行数、状态与 detail 与修改前一致。"""
    job = mtbf_fixture["job"]
    _attach_detail(db_session, job, tmp_path, {
        "run_dir": "20260831_120000",
        "metrics": {"passed": 1, "failed": 1},
        "testpoints": [
            {"name": "case_a", "status": "PASS", "testcases": [{"status": "PASS"}]},
            {
                "name": "case_b",
                "status": "FAILURE",
                "testcases": [{"status": "FAILURE", "message": "boom"}],
            },
        ],
    })

    count = ingest_test_case_results_for_job(db_session, job.id)
    db_session.commit()
    assert count == 2
    rows, total, summary = list_plan_run_test_case_results(
        db_session, mtbf_fixture["plan_run"].id,
    )
    assert total == 2
    assert summary["passed"] == 1
    assert summary["failed"] == 1
    by_name = {r.case_name: r for r in rows}
    assert by_name["case_a"].status == "PASS"
    assert by_name["case_a"].detail is None
    assert by_name["case_b"].status == "FAILURE"
    assert by_name["case_b"].detail == "boom"
    assert "执行" not in (by_name["case_b"].detail or "")


def test_ingest_synthetic_166760_shape_dup_at_head_3700(
    db_session, mtbf_fixture, tmp_path,
):
    """A4: 最小合成夹具（重名位于第 0、1 条），形态对齐 job 166760，不含生产数据。"""
    job = mtbf_fixture["job"]
    dup_name = "synthetic_retry_case"
    _attach_detail(db_session, job, tmp_path, {
        "run_dir": "2026.01.01_00.00.00.000",
        "metrics": {"entries": 3, "passed": 1, "failed": 2},
        "testpoints": [
            {
                "name": dup_name,
                "status": "FAILURE",
                "testcases": [{"status": "FAILURE", "message": "attempt-0"}],
            },
            {
                "name": dup_name,
                "status": "FAILURE",
                "testcases": [{"status": "FAILURE", "message": "attempt-1"}],
            },
            {
                "name": "other_case",
                "status": "PASS",
                "testcases": [{"status": "PASS"}],
            },
        ],
    }, name="synthetic_166760_shape.json")

    count = ingest_test_case_results_for_job(db_session, job.id)
    db_session.commit()
    assert count == 2
    rows, total, summary = list_plan_run_test_case_results(
        db_session, mtbf_fixture["plan_run"].id,
    )
    assert total == 2
    assert summary["failed"] == 1
    assert summary["passed"] == 1
    by_name = {r.case_name: r for r in rows}
    assert by_name[dup_name].status == "FAILURE"
    assert "执行 2 次" in (by_name[dup_name].detail or "")
    assert "attempt-1" in (by_name[dup_name].detail or "")
    assert by_name["other_case"].status == "PASS"
