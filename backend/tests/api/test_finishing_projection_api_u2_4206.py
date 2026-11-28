"""B12 U2：finishing 列表筛选 / Minimap facets / 升级门禁提示（API 级）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.agent.contracts.finishing_guard import (
    FINISHING_KIND_ATOMIC_COMMIT,
    GUARD_STATE_GRANTED,
)
from backend.core.job_timeout_config import EXECUTING_DISARM
from backend.models.enums import DeviceStatus, HostStatus, JobStatus, PlanRunStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services.host_upgrade_gate import (
    HostAbortDrainTimeoutError,
    begin_host_upgrade,
)


def _seed_run(db, *, suffix: str) -> tuple[Plan, PlanRun, Host, Device]:
    plan = Plan(name=f"U2-Plan-{suffix}")
    db.add(plan)
    db.flush()
    run = PlanRun(
        plan_id=plan.id,
        status=PlanRunStatus.RUNNING.value,
        run_type="MANUAL",
        triggered_by="pytest-u2",
        plan_snapshot={"steps": []},
        run_context={},
    )
    db.add(run)
    db.flush()
    host = Host(
        id=f"u2-h-{suffix}",
        hostname=f"u2-{suffix}",
        name=f"U2Host-{suffix}",
        ip=f"10.77.{int(suffix[:2], 16) % 200}.{int(suffix[2:4], 16) % 200}",
        ip_address=f"10.77.{int(suffix[:2], 16) % 200}.{int(suffix[2:4], 16) % 200}",
        status=HostStatus.ONLINE.value,
        last_heartbeat=datetime.now(timezone.utc),
    )
    db.add(host)
    db.flush()
    device = Device(
        serial=f"U2SER-{suffix}",
        model="u2",
        status=DeviceStatus.BUSY.value,
        host_id=host.id,
        adb_state="device",
        adb_connected=True,
    )
    db.add(device)
    db.flush()
    return plan, run, host, device


def test_list_filter_finishing_total_matches_items_and_stats_keep_running(
    client, db_session, auth_headers,
):
    suffix = uuid4().hex[:6]
    _plan, run, host, device = _seed_run(db_session, suffix=suffix)
    job = JobInstance(
        plan_run_id=run.id,
        plan_id=run.plan_id,
        device_id=device.id,
        host_id=host.id,
        status=JobStatus.RUNNING.value,
        execution_state=EXECUTING_DISARM,
        disarm_guard={
            "protocol": 1,
            "t0": datetime.now(timezone.utc).timestamp(),
            "job_deadline": datetime.now(timezone.utc).timestamp() + 600,
            "seq": 1,
        },
        pipeline_def={},
    )
    db_session.add(job)
    db_session.commit()

    resp = client.get(
        "/api/v1/plan-runs",
        params=[("status", "finishing"), ("q", f"U2-Plan-{suffix}")],
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["total"] == len(body["items"])
    assert body["total"] >= 1
    ids = {item["id"] for item in body["items"]}
    assert run.id in ids
    matched = next(item for item in body["items"] if item["id"] == run.id)
    assert matched["status"] == PlanRunStatus.RUNNING.value
    assert matched["ui_status"] == "finishing"
    # stats.running 继续按持久化 RUNNING（D1.16），不受 finishing 筛选收缩。
    assert body["stats"]["running"] >= 1


def test_minimap_by_status_includes_finishing_and_seven_fields(
    client, db_session, auth_headers,
):
    suffix = uuid4().hex[:6]
    _plan, run, host, device = _seed_run(db_session, suffix=suffix)
    job = JobInstance(
        plan_run_id=run.id,
        plan_id=run.plan_id,
        device_id=device.id,
        host_id=host.id,
        status=JobStatus.RUNNING.value,
        execution_state=EXECUTING_DISARM,
        disarm_guard={
            "protocol": 1,
            "t0": datetime.now(timezone.utc).timestamp(),
            "job_deadline": datetime.now(timezone.utc).timestamp() + 600,
            "seq": 1,
        },
        pipeline_def={},
        log_signal_count=2,
    )
    db_session.add(job)
    db_session.commit()

    resp = client.get(
        f"/api/v1/plan-runs/{run.id}/devices/minimap",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["facets"]["by_status"].get("finishing", 0) >= 1
    expected = {
        "job_id",
        "device_id",
        "device_serial",
        "host_id",
        "job_exec_status",
        "device_link_status",
        "log_signal_count",
    }
    for item in data["devices"]:
        assert set(item.keys()) == expected
    by_jid = {d["job_id"]: d for d in data["devices"]}
    assert by_jid[job.id]["job_exec_status"] == "finishing"


def test_gate_drain_timeout_detail_includes_finishing_hint(
    db_session, sample_host, sample_running_job,
):
    from backend.models.device_lease import DeviceLease
    from backend.models.enums import LeaseStatus, LeaseType

    now = datetime.now(timezone.utc)
    stamp = now.timestamp()
    sample_host.boot_id = "u2-boot"
    sample_host.last_agent_instance_id = "u2-agent"
    sample_host.last_heartbeat = now
    sample_host.status = "ONLINE"
    sample_running_job.finishing_guard = {
        "kind": FINISHING_KIND_ATOMIC_COMMIT,
        "guard_state": GUARD_STATE_GRANTED,
        "trigger": "manual_abort",
        "D_atomic": stamp + 1800,
        "last_liveness_receipt_at": stamp,
        "last_progress_receipt_at": stamp,
        "last_server_at": stamp,
        "fencing_token": "u2-fence",
        "agent_instance_id": "u2-agent",
        "boot_id": "u2-boot",
    }
    db_session.add(DeviceLease(
        device_id=sample_running_job.device_id,
        host_id=sample_host.id,
        job_id=sample_running_job.id,
        status=LeaseStatus.ACTIVE.value,
        lease_type=LeaseType.JOB.value,
        lease_generation=1,
        fencing_token="u2-fence",
        agent_instance_id="u2-agent",
        expires_at=now + timedelta(minutes=20),
    ))
    pr = db_session.get(PlanRun, sample_running_job.plan_run_id)
    assert pr is not None
    pr.run_context = {
        "abort_requested": {
            "at": now.isoformat(),
            "requested_job_ids": [sample_running_job.id],
        }
    }
    db_session.commit()

    with pytest.raises(HostAbortDrainTimeoutError) as excinfo:
        begin_host_upgrade(
            db_session,
            sample_host.id,
            holder="test:u2-timeout",
            abort_running_jobs=True,
            drain_timeout_seconds=0.2,
        )

    err = excinfo.value
    assert sample_running_job.id in err.lingering_jobs
    assert err.detail.get("finishing_kind") == FINISHING_KIND_ATOMIC_COMMIT
    assert "suggested_retry_window_seconds" in err.detail
    assert err.abort_summary.get("finishing_kind") == FINISHING_KIND_ATOMIC_COMMIT
