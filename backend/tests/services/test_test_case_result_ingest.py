"""test_case_result 摄入与查询。"""

import json
from datetime import datetime, timezone

import pytest

from backend.models.job import JobInstance, StepTrace
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.suite import TestCase, TestSuite
from backend.services.test_case_result_ingest import (
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
