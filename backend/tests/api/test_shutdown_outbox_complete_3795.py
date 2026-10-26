"""B6 U10: a real shutdown outbox payload reaches the real /complete API."""

import os
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from backend.agent import graceful_shutdown
from backend.agent.job_runner import JobRunnerState
from backend.agent.lease_renewer import LeaseRenewer
from backend.agent.registry.local_db import LocalDB
from backend.core import task_queue
from backend.core.database import SessionLocal
from backend.models.device_lease import DeviceLease
from backend.models.device_return_debt import DeviceReturnDebt
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services.device_return import DEBT_LEASE_HOLDER, DEBT_LEASE_REASON


PIPELINE = {
    "lifecycle": {
        "init": [{"step_id": "setup", "action": "script:powercycle_setup"}],
        "teardown": [
            {"step_id": "disarm", "action": "script:powercycle_disarm", "version": "1.0.8"},
            {"step_id": "finish", "action": "script:powercycle_finish", "version": "1.1.0"},
        ],
    },
}


@pytest.fixture(autouse=True)
def isolate_side_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("shutdown integration tests cannot signal the host")

    monkeypatch.setattr(os, "kill", forbidden)
    monkeypatch.setattr(os, "killpg", forbidden)
    # The completion/lease transaction is real; external post-processing is not
    # part of this acceptance and must not reach a host Redis service.
    monkeypatch.setattr(
        task_queue, "get_queue", lambda: SimpleNamespace(enqueue=AsyncMock()),
    )


def _seed(db, status):
    now = datetime.now(timezone.utc)
    suffix = uuid4().hex
    host = Host(
        id=f"u10-{suffix}", hostname=f"u10-{suffix}", status="ONLINE",
        last_heartbeat=now, boot_id="u10-test-boot",
        last_agent_instance_id="u10-test-instance",
    )
    device = Device(
        serial=f"U10-{suffix}", host_id=host.id, status="BUSY", tags=[],
        model="U10_TEST_MODEL", platform="TEST", lease_generation=1,
    )
    plan = Plan(name=f"u10-{suffix}", created_by="test")
    db.add_all([host, device, plan])
    db.flush()
    run = PlanRun(
        plan_id=plan.id, status="RUNNING", plan_snapshot={"name": plan.name},
        run_type="MANUAL", triggered_by="test",
    )
    db.add(run)
    db.flush()
    job = JobInstance(
        plan_run_id=run.id, plan_id=plan.id, device_id=device.id, host_id=host.id,
        status=status, pipeline_def=PIPELINE, started_at=now-timedelta(minutes=1),
        ended_at=now-timedelta(seconds=30) if status == "UNKNOWN" else None,
        execution_state="EXECUTING_DISARM",
    )
    db.add(job)
    db.flush()
    token = f"{device.id}:1"
    lease = DeviceLease(
        device_id=device.id, job_id=job.id, host_id=host.id, lease_type="JOB",
        status="ACTIVE", fencing_token=token, lease_generation=1,
        agent_instance_id="u10-test-instance", acquired_at=now, renewed_at=now,
        # UNKNOWN keeps an expired ACTIVE lease inside its recovery grace.
        expires_at=now+timedelta(minutes=5) if status == "RUNNING" else now-timedelta(seconds=5),
    )
    db.add(lease)
    db.commit()
    return job.id, device.id, lease.id, token


def _shutdown_payload(monkeypatch, tmp_path, job_id, device_id, token, source):
    local_db = LocalDB()
    local_db.initialize(str(tmp_path / "agent-shutdown.sqlite"))
    if source == "registry":
        local_db.save_active_job(job_id, device_id, token)
    worker_token = f"worker-{job_id}-u10-test"
    state = JobRunnerState(
        active_jobs_lock=threading.RLock(), active_job_ids={job_id},
        active_device_ids={device_id}, active_job_tokens={job_id: worker_token},
        running_worker_tokens={job_id: worker_token}, watcher_globally_enabled=False,
        watcher_plan_default=False, lock_register=MagicMock(), lock_deregister=MagicMock(),
        device_id_register=MagicMock(), device_id_deregister=MagicMock(),
    )
    runner = SimpleNamespace(
        _footprint_families={"power-cycle"},
        _disarm_results={"power-cycle": {"step_id": "disarm"}}, cancel=MagicMock(),
    )
    state.attach_runner(job_id, worker_token, runner)
    renewer = LeaseRenewer(
        "http://example.invalid", state.active_jobs_lock, state.active_job_ids,
        threading.Event(),
    )
    renewer.set_fencing_token(job_id, token, device_id, worker_token)
    captured = []
    outbox = MagicMock()

    def capture_drain():
        captured.extend(local_db.get_pending_terminals())
        return len(captured)

    outbox.drain_sync.side_effect = capture_drain
    wait = MagicMock()
    monkeypatch.setattr(graceful_shutdown, "_await_started_disarm", wait)
    for name in ("LocalDiskMonitor", "LogArchiver", "EventUploader"):
        monkeypatch.setattr(graceful_shutdown, name, MagicMock())
    try:
        graceful_shutdown.shutdown_agent_runtime(
            coordinator=MagicMock(), operation_scheduler=MagicMock(),
            job_runner_state=state, executor=MagicMock(),
            step_trace_uploader=MagicMock(), outbox_drain=outbox,
            log_signal_drainer=None, recovery_sync_stop=MagicMock(),
            recovery_sync_thread=MagicMock(), heartbeat_thread=MagicMock(),
            lease_renewer=renewer, mq_producer=MagicMock(), local_db=local_db,
            sio_client=MagicMock(),
        )
    finally:
        local_db.close()
    wait.assert_called_once_with(state, timeout_seconds=20.0)
    assert len(captured) == 1
    assert captured[0]["attempts"] == 0
    return captured[0]["payload"], worker_token


@pytest.mark.parametrize("status", ["RUNNING", "UNKNOWN"])
@pytest.mark.parametrize("token_source", ["registry", "renewer"])
def test_shutdown_outbox_complete_converts_same_job_lease_to_debt(
    client, db_session, monkeypatch, tmp_path, status, token_source,
):
    job_id, device_id, lease_id, lease_token = _seed(db_session, status)
    payload, worker_token = _shutdown_payload(
        monkeypatch, tmp_path, job_id, device_id, lease_token, token_source,
    )
    assert payload["update"]["status"] == "CANCELED"
    assert payload["fencing_token"] == lease_token
    entry = payload["disarm_results"]["power-cycle"]
    assert entry["result"] == "incomplete"
    assert entry["reason"] == "shutdown_disarm_window_elapsed"
    url = f"/api/v1/agent/jobs/{job_id}/complete"

    rejected = client.post(url, json={**payload, "fencing_token": worker_token})
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["detail"]["code"] == "INVALID_OR_EXPIRED_FENCING_TOKEN"
    with SessionLocal() as reader:
        assert reader.get(JobInstance, job_id).status == status
        assert reader.get(DeviceLease, lease_id).lease_type == "JOB"
        assert reader.query(DeviceReturnDebt).filter_by(device_id=device_id).count() == 0

    response = client.post(url, json=payload)
    assert response.status_code == 200, response.text
    with SessionLocal() as reader:
        job = reader.get(JobInstance, job_id)
        assert job.status == "ABORTED"
        assert job.disarm_results["power-cycle"]["result"] == "incomplete"
        assert job.disarm_results["power-cycle"]["reason"] == entry["reason"]
        debt = reader.query(DeviceReturnDebt).filter_by(device_id=device_id).one()
        assert (debt.family, debt.source_kind, debt.source_job_id) == (
            "power-cycle", "job", job_id,
        )
        assert (debt.cause, debt.state) == ("incomplete", "open")
        active = reader.query(DeviceLease).filter_by(device_id=device_id, status="ACTIVE").one()
        assert active.id == lease_id
        assert (active.lease_type, active.job_id) == ("MAINTENANCE", None)
        assert (active.holder, active.reason) == (DEBT_LEASE_HOLDER, DEBT_LEASE_REASON)
        assert reader.get(Device, device_id).return_state["power-cycle"]["state"] == "debt"
