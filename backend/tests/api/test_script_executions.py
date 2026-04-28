"""Script execution facade API tests."""

from uuid import uuid4

from backend.models.job import JobInstance
from backend.models.workflow import WorkflowRun


def _uniq(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


def _script_payload(name: str) -> dict:
    return {
        "name": name,
        "display_name": name,
        "category": "device",
        "script_type": "python",
        "version": "1.0.0",
        "nfs_path": f"/mnt/storage/test-platform/scripts/device/{name}/v1.0.0/{name}.py",
        "entry_point": "",
        "content_sha256": "c" * 64,
        "param_schema": {"duration": {"type": "integer"}},
        "description": "Runnable script",
        "is_active": True,
    }


def test_script_execution_creates_workflow_run_and_job_instances(
    client,
    db_session,
    sample_device,
):
    script_name = _uniq("run_monkey")
    script_resp = client.post("/api/v1/scripts", json=_script_payload(script_name))
    assert script_resp.status_code == 201

    resp = client.post(
        "/api/v1/script-executions",
        json={
            "items": [
                {
                    "script_name": script_name,
                    "version": "1.0.0",
                    "params": {"duration": 60},
                    "timeout_seconds": 120,
                    "retry": 0,
                }
            ],
            "device_ids": [sample_device.id],
            "on_failure": "stop",
        },
    )

    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["workflow_run_id"] > 0
    assert data["device_count"] == 1
    assert data["step_count"] == 1
    assert len(data["job_ids"]) == 1

    run = db_session.get(WorkflowRun, data["workflow_run_id"])
    assert run.triggered_by == "script_execution"
    assert run.result_summary["mode"] == "script_execution"

    job = db_session.get(JobInstance, data["job_ids"][0])
    assert job.workflow_run_id == run.id
    assert job.device_id == sample_device.id
    assert job.host_id == sample_device.host_id
    assert job.status == "PENDING"
    step = job.pipeline_def["stages"]["execute"][0]
    assert step == {
        "step_id": f"script_0_{script_name}",
        "action": f"script:{script_name}",
        "version": "1.0.0",
        "params": {"duration": 60},
        "timeout_seconds": 120,
        "retry": 0,
        "enabled": True,
    }


def test_script_execution_detail_returns_jobs_and_steps(client, sample_device):
    script_name = _uniq("collect_logs")
    assert client.post("/api/v1/scripts", json=_script_payload(script_name)).status_code == 201
    create_resp = client.post(
        "/api/v1/script-executions",
        json={
            "items": [
                {
                    "script_name": script_name,
                    "version": "1.0.0",
                    "params": {},
                    "timeout_seconds": 60,
                }
            ],
            "device_ids": [sample_device.id],
        },
    )
    assert create_resp.status_code == 201
    run_id = create_resp.json()["data"]["workflow_run_id"]

    detail_resp = client.get(f"/api/v1/script-executions/{run_id}")

    assert detail_resp.status_code == 200
    detail = detail_resp.json()["data"]
    assert detail["workflow_run_id"] == run_id
    assert detail["mode"] == "script_execution"
    assert len(detail["jobs"]) == 1
    assert detail["jobs"][0]["device_id"] == sample_device.id
    assert detail["jobs"][0]["device_serial"] == sample_device.serial
    assert detail["jobs"][0]["steps"][0]["script_name"] == script_name
