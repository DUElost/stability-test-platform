"""PlanRun report export endpoint tests (T-B5)."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.models.enums import JobStatus
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun

PIPELINE_DEF = {"lifecycle": {"init": [], "teardown": []}}


def test_export_plan_run_markdown(client, auth_headers, db_session, gate_chain):
    pr = PlanRun(
        plan_id=gate_chain["plan"].id,
        status="SUCCESS",
        failure_threshold=0.1,
        plan_snapshot={"plan_id": gate_chain["plan"].id, "name": gate_chain["plan"].name},
        run_type="MANUAL",
        triggered_by="test",
        started_at=datetime.now(timezone.utc),
        ended_at=datetime.now(timezone.utc),
        result_summary={"completed": 1, "total": 1},
    )
    db_session.add(pr)
    db_session.flush()
    db_session.add(
        JobInstance(
            plan_run_id=pr.id,
            plan_id=gate_chain["plan"].id,
            device_id=gate_chain["device_a"].id,
            host_id=gate_chain["host_a"].id,
            status=JobStatus.COMPLETED.value,
            pipeline_def=PIPELINE_DEF,
        )
    )
    db_session.commit()

    resp = client.get(
        f"/api/v1/plan-runs/{pr.id}/report/export?format=markdown",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert "text/plain" in resp.headers.get("content-type", "")
    assert f"PlanRun #{pr.id} Report" in resp.text
    assert "SUCCESS" in resp.text
    assert "attachment" in resp.headers.get("content-disposition", "")


def test_export_plan_run_json(client, auth_headers, db_session, gate_chain):
    pr = PlanRun(
        plan_id=gate_chain["plan"].id,
        status="FAILED",
        failure_threshold=0.1,
        plan_snapshot={"plan_id": gate_chain["plan"].id},
        run_type="MANUAL",
        triggered_by="test",
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(pr)
    db_session.commit()

    resp = client.get(
        f"/api/v1/plan-runs/{pr.id}/report/export?format=json",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["plan_run_id"] == pr.id
    assert body["status"] == "FAILED"
    assert "summary" in body
    assert "devices" in body
