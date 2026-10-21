"""B6 U8 real entry/transaction tests; no devices or host signals."""
from datetime import datetime, timedelta, timezone
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.pool import NullPool
import os

import pytest

from backend.core.database import SessionLocal
from backend.models.device_lease import DeviceLease
from backend.models.host import Host
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun
from backend.scheduler.device_lease_reconciler import _abort_reaper_recheck_job
from backend.scheduler import recycler
from backend.services.device_return import manifest_package_sha
from backend.services.plan_run_devices import running_heartbeat_deadline
from backend.tests.scheduler.test_recycler import _seed_running_job, _cleanup_seed

PIPE = {"lifecycle": {"init": [{"step_id": "setup", "action": "script:powercycle_setup"}],
        "teardown": [{"step_id": "disarm", "action": "script:powercycle_disarm", "version": "1.0.8", "timeout_seconds": 660}]}}


@pytest.fixture(autouse=True)
def signals_are_forbidden(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("B6 U8 tests cannot signal the host")
    monkeypatch.setattr(os, "kill", forbidden)
    monkeypatch.setattr(os, "killpg", forbidden)
    monkeypatch.setattr(recycler, "schedule_emit", lambda *a, **k: None)


@pytest.fixture
def seed(db_session):
    now = datetime.now(timezone.utc)
    seed = _seed_running_job(now - timedelta(hours=2), now, pipeline_def=PIPE,
                             execution_state="WAITING_EXECUTION_SLOT")
    with SessionLocal() as db:
        host = db.get(Host, seed["host_id"])
        host.boot_id = "u8-test-boot"
        host.last_agent_instance_id = "u8-test-instance"
        run = db.get(PlanRun, seed["plan_run_id"])
        run.run_context = {"abort_requested": {"at": (now-timedelta(seconds=61)).isoformat()}}
        db.add(DeviceLease(device_id=seed["device_id"], job_id=seed["job_id"], host_id=seed["host_id"],
                           lease_type="JOB", status="ACTIVE", fencing_token="u8-test-token",
                           lease_generation=1, agent_instance_id="u8-test-instance", expires_at=now+timedelta(hours=1)))
        db.commit()
    yield seed
    _cleanup_seed(seed)


def heartbeat(client, seed, seq=1, phase="queue", progress=0):
    return client.post("/api/v1/agent/coordinator-heartbeat", json={
        "host_id": seed["host_id"], "agent_instance_id": "u8-test-instance", "plan_run_hosts": [],
        "jobs": [{"job_id": seed["job_id"], "execution_state": "WAITING_EXECUTION_SLOT" if phase=="queue" else "EXECUTING_DISARM",
                  "disarm_evidence": {"protocol": 1, "step_id": "disarm", "phase": phase, "seq": seq,
                  "progress_seq": progress, "script_name": "powercycle_disarm", "script_version": "1.0.8",
                  "package_sha256": manifest_package_sha("powercycle_disarm", "1.0.8"), "fencing_token": "u8-test-token"}}]})


def test_actual_queue_heartbeat_reaper_extend_after_abort_61_seconds(client, seed):
    hb = heartbeat(client, seed)
    assert hb.status_code == 200, hb.text
    assert str(seed["job_id"]) in hb.json()["data"]["disarm_guards"], hb.text
    assert not run_reaper(seed, datetime.now(timezone.utc))
    response = client.post("/api/v1/agent/leases/extend-batch", json={
        "host_id": seed["host_id"], "agent_instance_id": "u8-test-instance", "leases": [
            {"job_id": seed["job_id"], "fencing_token": "u8-test-token", "execution_state": "WAITING_EXECUTION_SLOT"}]})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["results"][0]["status"] == "renewed"
    with SessionLocal() as db:
        row = db.get(JobInstance, seed["job_id"])
        assert row.status == "RUNNING"
        assert row.last_progress_at is None
        assert running_heartbeat_deadline(row, db=db) > datetime.now(timezone.utc)


@pytest.mark.parametrize("entry", ["running", "patrol", "trace"])
def test_recycler_final_entry_rereads_committed_guard_after_stale_candidate(client, seed, entry):
    with SessionLocal() as candidate_db:
        candidate = candidate_db.get(JobInstance, seed["job_id"])
        assert candidate.disarm_guard is None
        assert heartbeat(client, seed).status_code == 200
        now = datetime.now(timezone.utc)
        if entry == "running":
            changed = recycler._mark_running_timeout(candidate_db, candidate, now, "u8-stale-test")
        elif entry == "patrol":
            changed = recycler._mark_patrol_stall(candidate_db, candidate, now, interval_seconds=60,
                                                age_seconds=7200, reason="u8-stale-test")
        else:
            changed = recycler._mark_step_trace_stall(candidate_db, candidate, now,
                last_ts=now-timedelta(hours=2), event_type="COMPLETED", reason="u8-stale-test")
        candidate_db.commit()
        assert not changed
    with SessionLocal() as verify:
        assert verify.get(JobInstance, seed["job_id"]).status == "RUNNING"


@pytest.mark.parametrize("broken", ["expired", "host", "boot", "lease", "token", "execute_without_progress"])
def test_invalid_guard_does_not_block_actual_reaper(client, seed, broken):
    assert heartbeat(client, seed).status_code == 200
    now = datetime.now(timezone.utc)
    with SessionLocal() as writer:
        job = writer.get(JobInstance, seed["job_id"])
        if broken == "expired":
            job.disarm_guard = {**job.disarm_guard, "job_deadline": now.timestamp()-1}
        elif broken == "host":
            writer.get(Host, seed["host_id"]).last_heartbeat = now-timedelta(hours=1)
        elif broken == "boot":
            writer.get(Host, seed["host_id"]).boot_id = "changed-boot"
        elif broken == "lease":
            writer.query(DeviceLease).filter(DeviceLease.job_id==seed["job_id"]).update({"expires_at": now-timedelta(seconds=1)})
        elif broken == "token":
            writer.query(DeviceLease).filter(DeviceLease.job_id==seed["job_id"]).update({"fencing_token": "other-token"})
        writer.commit()
    if broken == "execute_without_progress":
        assert heartbeat(client, seed, seq=2, phase="execute").status_code == 200
    assert run_reaper(seed, datetime.now(timezone.utc))


def run_reaper(seed, now):
    async def run():
        engine = create_async_engine(os.environ["TEST_DATABASE_URL"].replace("+psycopg", "+asyncpg"), poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as db:
                changed, _ = await _abort_reaper_recheck_job(db, seed["job_id"], now, abort_at=now-timedelta(seconds=61))
                await db.commit()
                return changed
        finally:
            await engine.dispose()
    return asyncio.run(run())


def test_expired_queue_is_selected_and_recycled_despite_fresh_generic_coordinator(client, seed, monkeypatch):
    from backend.tests.scheduler.test_recycler import _seed_fresh_coordinator_heartbeat
    now = datetime.now(timezone.utc)
    _seed_fresh_coordinator_heartbeat(seed, at=now)
    assert heartbeat(client, seed).status_code == 200
    with SessionLocal() as writer:
        job = writer.get(JobInstance, seed["job_id"])
        job.disarm_guard = {**job.disarm_guard, "job_deadline": now.timestamp()-1}
        writer.commit()
    monkeypatch.setattr(recycler, "_fill_deferred_post_completions", lambda *_: 0)
    monkeypatch.setattr(recycler, "_prune_steptrace_artifacts", lambda *_: None)
    recycler.recycle_once()
    with SessionLocal() as verify:
        assert verify.get(JobInstance, seed["job_id"]).status == "UNKNOWN"
