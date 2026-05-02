"""Script execution facade API tests."""

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from backend.core.database import AsyncSessionLocal, SessionLocal, async_engine
from backend.models.enums import HostStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.script import Script
from backend.models.workflow import WorkflowRun
from backend.services.script_execution import create_script_execution

pytestmark_claim = pytest.mark.skipif(
    os.getenv("DATABASE_URL", "").startswith("sqlite"),
    reason="claim_jobs needs PostgreSQL (device_leases partial unique index)",
)


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


# ══════════════════════════════════════════════════════════════════════════════
# Phase 5a: enhanced list endpoint
# ══════════════════════════════════════════════════════════════════════════════


def test_list_includes_device_and_script_info(client, db_session, sample_host):
    """Phase 5a: list 响应含 device_count / device_serials / script_names / host_name。

    创建 5 台设备 + 2 个脚本 → 断言 device_count==5, device_serials 截断为前 3 台,
    script_names 用 " → " 连接。
    """
    device_ids = []
    for i in range(5):
        serial = f"DEV-{i+1:03d}"
        device = Device(
            serial=serial,
            host_id=sample_host.id,
            status="ONLINE",
            last_seen=datetime.utcnow(),
            adb_connected=True,
            adb_state="device",
        )
        db_session.add(device)
        db_session.flush()
        device_ids.append(device.id)

    script1 = _uniq("monkey")
    script2 = _uniq("collect_logs")
    assert client.post("/api/v1/scripts", json=_script_payload(script1)).status_code == 201
    assert client.post("/api/v1/scripts", json=_script_payload(script2)).status_code == 201

    resp = client.post(
        "/api/v1/script-executions",
        json={
            "items": [
                {"script_name": script1, "version": "1.0.0", "params": {}, "timeout_seconds": 60},
                {"script_name": script2, "version": "1.0.0", "params": {}, "timeout_seconds": 60},
            ],
            "device_ids": device_ids,
        },
    )
    assert resp.status_code == 201

    list_resp = client.get("/api/v1/script-executions")
    assert list_resp.status_code == 200
    items = list_resp.json()["data"]["items"]
    assert len(items) >= 1

    item = items[0]
    assert item["device_count"] == 5
    assert len(item["device_serials"]) == 3
    assert item["device_serials"] == ["DEV-001", "DEV-002", "DEV-003"]
    assert item["script_names"] == f"{script1} → {script2}"
    assert item["host_name"] == (sample_host.name or sample_host.hostname)


def test_rerun_returns_new_workflow_run_id(client, db_session, sample_device):
    """Phase 5a: rerun 创建新 WorkflowRun，返回不同的 workflow_run_id。"""
    from backend.models.enums import JobStatus

    script_name = _uniq("rerun_test")
    assert client.post("/api/v1/scripts", json=_script_payload(script_name)).status_code == 201

    create_resp = client.post(
        "/api/v1/script-executions",
        json={
            "items": [
                {"script_name": script_name, "version": "1.0.0", "params": {}, "timeout_seconds": 60}
            ],
            "device_ids": [sample_device.id],
        },
    )
    assert create_resp.status_code == 201
    run_id_1 = create_resp.json()["data"]["workflow_run_id"]
    job_id = create_resp.json()["data"]["job_ids"][0]

    # Mark job as terminal so rerun guard allows it
    job = db_session.get(JobInstance, job_id)
    job.status = JobStatus.COMPLETED.value
    db_session.commit()

    rerun_resp = client.post(f"/api/v1/script-executions/{run_id_1}/rerun")
    assert rerun_resp.status_code == 201
    run_id_2 = rerun_resp.json()["data"]["workflow_run_id"]

    assert run_id_2 != run_id_1


def test_list_filters_by_result_summary_mode(
    client, db_session, sample_workflow_definition, sample_device
):
    """Phase 5a: 仅 result_summary.mode == "script_execution" 的 WorkflowRun 出现在列表中。

    创建 trigger_by="script_execution" 但 result_summary 无 mode 字段的 WorkflowRun
    → list 不返回该行；有 mode 的正常返回。
    """
    script_name = _uniq("mode_filter")
    assert client.post("/api/v1/scripts", json=_script_payload(script_name)).status_code == 201

    resp = client.post(
        "/api/v1/script-executions",
        json={
            "items": [
                {"script_name": script_name, "version": "1.0.0", "params": {}, "timeout_seconds": 60}
            ],
            "device_ids": [sample_device.id],
        },
    )
    assert resp.status_code == 201
    real_run_id = resp.json()["data"]["workflow_run_id"]

    # WorkflowRun with triggered_by="script_execution" but NO "mode" in result_summary
    run_no_mode = WorkflowRun(
        workflow_definition_id=sample_workflow_definition.id,
        status="RUNNING",
        failure_threshold=0.0,
        triggered_by="script_execution",
        started_at=datetime.utcnow(),
        result_summary={"items": [{"script_name": "fake", "version": "1.0"}]},
    )
    db_session.add(run_no_mode)
    db_session.commit()

    list_resp = client.get("/api/v1/script-executions")
    assert list_resp.status_code == 200
    items = list_resp.json()["data"]["items"]

    run_ids = [item["workflow_run_id"] for item in items]
    assert real_run_id in run_ids
    assert run_no_mode.id not in run_ids


# ══════════════════════════════════════════════════════════════════════════════
# 5a-0: E2E — script_execution JobInstance is claimable
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytestmark_claim
async def test_script_execution_job_is_claimable():
    """5a-0: script_execution → PENDING JobInstance → claim_jobs 正常返回。

    验证 script_execution 创建的 JobInstance 能被 _claim_jobs_for_host 正常领取：
    - 返回的 job 含 script:<name> pipeline_def
    - 返回的 fencing_token 非空
    - job 状态变为 RUNNING
    """
    from backend.api.routes.agent_api import _claim_jobs_for_host
    from backend.models.device_lease import DeviceLease

    suffix = uuid4().hex[:8]
    script_name = f"e2e_claim_{suffix}"
    host_id = f"e2e-host-{suffix}"
    now = datetime.now(timezone.utc)

    # 1. Create host + device + active script via SessionLocal
    db = SessionLocal()
    try:
        host = Host(
            id=host_id, hostname=f"h-{suffix}",
            status=HostStatus.ONLINE.value, created_at=now,
        )
        device = Device(
            serial=f"E2E-{suffix}", host_id=host_id,
            status="ONLINE", tags=[], created_at=now,
            adb_connected=True, adb_state="device",
        )
        db.add_all([host, device])
        db.flush()
        device_id = device.id

        script = Script(
            name=script_name,
            display_name=script_name,
            category="device",
            script_type="python",
            version="1.0.0",
            nfs_path=f"/mnt/storage/scripts/{script_name}.py",
            entry_point="",
            content_sha256="e" * 64,
            param_schema={"duration": {"type": "integer"}},
            description="E2E claim test script",
            is_active=True,
        )
        db.add(script)
        db.flush()

        # 2. Create script execution → PENDING JobInstance
        result = create_script_execution(
            db,
            items=[{
                "script_name": script_name,
                "version": "1.0.0",
                "params": {"duration": 60},
                "timeout_seconds": 120,
                "retry": 0,
            }],
            device_ids=[device_id],
            sequence_id=None,
            on_failure="stop",
        )
        job_id = result["job_ids"][0]
        workflow_run_id = result["workflow_run_id"]
        db.commit()
    finally:
        db.close()

    try:
        # 3. Claim via _claim_jobs_for_host
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            jobs, token_map = await _claim_jobs_for_host(
                db=async_db, host_id=host_id, capacity=5,
            )

            assert len(jobs) == 1, f"Expected 1 claimed job, got {len(jobs)}"
            job = jobs[0]
            assert job.id == job_id
            assert job.status == "RUNNING"

            # pipeline_def must contain script:<name> action
            step = job.pipeline_def["stages"]["execute"][0]
            assert step["action"] == f"script:{script_name}", (
                f"Expected script:{script_name}, got {step['action']}"
            )

            # fencing_token must be non-empty
            token = token_map.get(job_id)
            assert token is not None, "fencing_token must not be None"
            assert token != "", "fencing_token must not be empty"
            assert ":" in token, f"fencing_token format wrong: {token}"

    finally:
        # Cleanup (FK order: lease → job → run; don't touch system anchor)
        db = SessionLocal()
        try:
            db.query(DeviceLease).filter(DeviceLease.job_id == job_id).delete()
            db.query(JobInstance).filter(JobInstance.id == job_id).delete()
            db.query(WorkflowRun).filter(WorkflowRun.id == workflow_run_id).delete()
            db.query(Script).filter(Script.name == script_name).delete()
            db.query(Device).filter(Device.id == device_id).delete()
            db.query(Host).filter(Host.id == host_id).delete()
            db.commit()
        finally:
            db.close()


# ══════════════════════════════════════════════════════════════════════════════
# Phase 5: active-job guard (create / rerun conflict → 409)
# ══════════════════════════════════════════════════════════════════════════════


def test_rerun_non_terminal_returns_409(client, sample_device):
    """重跑未终态的执行 → 409 Conflict。"""
    script_name = _uniq("rerun_conflict")
    assert client.post("/api/v1/scripts", json=_script_payload(script_name)).status_code == 201

    create_resp = client.post(
        "/api/v1/script-executions",
        json={
            "items": [
                {"script_name": script_name, "version": "1.0.0", "params": {}, "timeout_seconds": 60}
            ],
            "device_ids": [sample_device.id],
        },
    )
    assert create_resp.status_code == 201
    run_id = create_resp.json()["data"]["workflow_run_id"]

    # Job is still PENDING → rerun must return 409
    rerun_resp = client.post(f"/api/v1/script-executions/{run_id}/rerun")
    assert rerun_resp.status_code == 409


def test_create_duplicate_active_device_returns_409(client, sample_device):
    """同设备已有活跃 job 时再次 create → 409 Conflict。"""
    script_name = _uniq("dup_conflict")
    assert client.post("/api/v1/scripts", json=_script_payload(script_name)).status_code == 201

    payload = {
        "items": [
            {"script_name": script_name, "version": "1.0.0", "params": {}, "timeout_seconds": 60}
        ],
        "device_ids": [sample_device.id],
    }

    resp1 = client.post("/api/v1/script-executions", json=payload)
    assert resp1.status_code == 201

    # Second create on same device (job still PENDING) → 409
    resp2 = client.post("/api/v1/script-executions", json=payload)
    assert resp2.status_code == 409


def test_rerun_after_terminal_succeeds(client, db_session, sample_device):
    """终态 job 后 rerun → 201 Created（新 workflow_run_id）。"""
    from backend.models.enums import JobStatus

    script_name = _uniq("rerun_terminal")
    assert client.post("/api/v1/scripts", json=_script_payload(script_name)).status_code == 201

    create_resp = client.post(
        "/api/v1/script-executions",
        json={
            "items": [
                {"script_name": script_name, "version": "1.0.0", "params": {}, "timeout_seconds": 60}
            ],
            "device_ids": [sample_device.id],
        },
    )
    assert create_resp.status_code == 201
    run_id = create_resp.json()["data"]["workflow_run_id"]
    job_id = create_resp.json()["data"]["job_ids"][0]

    # Manually mark job as terminal (COMPLETED)
    job = db_session.get(JobInstance, job_id)
    job.status = JobStatus.COMPLETED.value
    db_session.commit()

    # Rerun on terminal job → 201 with new workflow_run_id
    rerun_resp = client.post(f"/api/v1/script-executions/{run_id}/rerun")
    assert rerun_resp.status_code == 201
    assert rerun_resp.json()["data"]["workflow_run_id"] != run_id
