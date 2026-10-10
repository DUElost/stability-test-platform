"""参数投影 HTTP 面：保存、草稿、preview、Run、Job、脚本。"""
from __future__ import annotations

import json

from backend.models.enums import JobStatus
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.script import Script

SENTINEL = "SENTINEL_PASSWORD"


def _script(db_session, name="check_device", version="1.0.0", **fields):
    row = Script(
        name=name,
        script_type="python",
        version=version,
        nfs_path=f"/nfs/{name}/{version}",
        content_sha256="ab" * 32,
        is_active=True,
        param_schema=fields.get("param_schema") or {},
        default_params=fields.get("default_params") or {},
    )
    db_session.add(row)
    db_session.commit()
    return row


def test_saved_and_draft_projection(client, auth_headers, db_session, sample_plan, sample_script):
    before = db_session.query(Plan).count()
    saved = client.get(
        f"/api/v1/plans/{sample_plan.id}/parameter-projection",
        headers=auth_headers,
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()["data"]
    assert body["layer"] == "L1"
    assert body["context"]["plan_id"] == sample_plan.id

    draft = client.post(
        "/api/v1/plans/parameter-projection",
        headers=auth_headers,
        json={"steps": [{
            "step_key": "s",
            "script_name": "check_device",
            "script_version": "v1.0.0",
            "stage": "init",
            "params": {"free": False},
        }]},
    )
    assert draft.status_code == 200, draft.text
    assert draft.json()["data"]["layer"] == "L1"
    assert draft.json()["data"]["context"]["plan_id"] is None
    assert db_session.query(Plan).count() == before
    assert db_session.query(PlanRun).count() == 0

    unauth = client.get(f"/api/v1/plans/{sample_plan.id}/parameter-projection")
    assert unauth.status_code == 401
    missing = client.get("/api/v1/plans/999999/parameter-projection", headers=auth_headers)
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "PLAN_NOT_FOUND"


def test_preview_attaches_projection_from_saved_plan(
    client, auth_headers, db_session, sample_plan, sample_script,
):
    resp = client.post(
        f"/api/v1/plans/{sample_plan.id}/run/preview",
        headers=auth_headers,
        json={"device_ids": [1]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "lifecycle" in data
    assert data["parameter_projection"]["layer"] == "L1"
    assert data["parameter_projection"]["context"]["plan_id"] == sample_plan.id


def test_plan_run_detail_is_l2_and_job_projection_is_scoped(
    client, auth_headers, db_session, sample_plan, sample_plan_run, sample_device, sample_host,
):
    sample_plan_run.plan_snapshot = {
        "plan": {"id": sample_plan.id, "name": sample_plan.name, "watcher_policy": {}},
        "steps": [],
    }
    sample_plan_run.run_context = {}
    job = JobInstance(
        plan_run_id=sample_plan_run.id,
        plan_id=sample_plan.id,
        device_id=sample_device.id,
        host_id=sample_host.id,
        status=JobStatus.PENDING.value,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
    )
    db_session.add(job)
    db_session.commit()

    detail = client.get(f"/api/v1/plan-runs/{sample_plan_run.id}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["parameter_projection"]["layer"] == "L2"

    listed = client.get("/api/v1/plan-runs?limit=10", headers=auth_headers)
    assert listed.status_code == 200
    assert listed.json()["data"]["items"][0]["parameter_projection"] is None

    job_resp = client.get(
        f"/api/v1/plan-runs/{sample_plan_run.id}/jobs/{job.id}/parameter-projection",
        headers=auth_headers,
    )
    assert job_resp.status_code == 200, job_resp.text
    assert job_resp.json()["data"]["layer"] == "L3"
    assert '"actual"' not in json.dumps(job_resp.json())
    missing_job = client.get(
        f"/api/v1/plan-runs/{sample_plan_run.id}/jobs/999999/parameter-projection",
        headers=auth_headers,
    )
    assert missing_job.status_code == 404
    assert missing_job.json()["detail"]["code"] == "JOB_NOT_FOUND"


def test_script_keeps_raw_fields_and_masks_projection(client, auth_headers, db_session):
    _script(
        db_session,
        param_schema={"password": {"type": "string", "default": SENTINEL}},
        default_params={"password": SENTINEL, "ssid": "lab"},
    )
    resp = client.get("/api/v1/scripts", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    row = resp.json()["data"][0]
    assert row["default_params"]["password"] == SENTINEL
    projected = json.dumps(row["parameter_projection"], ensure_ascii=False)
    assert SENTINEL not in projected
    assert row["parameter_projection"]["params"]
