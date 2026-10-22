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
from backend.models.job import JobInstance, StepTrace
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
    if entry == "trace":
        with SessionLocal() as writer:
            writer.add(StepTrace(job_id=seed["job_id"], step_id="old-step", stage="patrol", status="COMPLETED",
                                 event_type="COMPLETED", exit_code=0, original_ts=datetime.now(timezone.utc)-timedelta(hours=2)))
            writer.commit()
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


@pytest.mark.parametrize("broken", ["expired", "host", "boot", "lease", "token", "execute_without_progress", "evidence_stale", "queue_budget", "step_budget"])
def test_invalid_guard_does_not_block_actual_reaper(client, seed, broken):
    assert heartbeat(client, seed).status_code == 200
    now = datetime.now(timezone.utc)
    with SessionLocal() as writer:
        job = writer.get(JobInstance, seed["job_id"])
        if broken == "expired":
            job.disarm_guard = {**job.disarm_guard, "job_deadline": now.timestamp()-1}
        elif broken == "evidence_stale":
            job.disarm_guard = {**job.disarm_guard, "coordinator_seen_at": now.timestamp()-61}
        elif broken == "queue_budget":
            job.disarm_guard = {**job.disarm_guard, "queue_deadline": now.timestamp()-1}
        elif broken == "step_budget":
            job.disarm_guard = {**job.disarm_guard, "step_deadlines": {"disarm": now.timestamp()-1}}
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


@pytest.mark.parametrize("entry", ["running", "patrol", "trace"])
@pytest.mark.parametrize("transition", ["execute", "terminal", "new_abort"])
def test_final_writes_reread_committed_transition(client, seed, entry, transition):
    """Candidates are stale ORM objects; a separate transaction commits first."""
    with SessionLocal() as candidate_db:
        candidate = candidate_db.get(JobInstance, seed["job_id"])
        now = datetime.now(timezone.utc)
        if transition == "execute":
            assert heartbeat(client, seed, phase="execute", progress=1).status_code == 200
        else:
            if transition == "new_abort":
                assert heartbeat(client, seed).status_code == 200
                with SessionLocal() as reader:
                    original_deadline = reader.get(JobInstance, seed["job_id"]).disarm_guard["job_deadline"]
            with SessionLocal() as writer:
                if transition == "terminal":
                    writer.get(JobInstance, seed["job_id"]).status = "COMPLETED"
                else:
                    # A new exit trigger after collection cannot use the old grace.
                    run = writer.get(PlanRun, seed["plan_run_id"])
                    run.run_context = {"abort_requested": {"at": now.isoformat()}}
                writer.commit()
            if transition == "new_abort":
                assert heartbeat(client, seed, seq=2).status_code == 200
                with SessionLocal() as reader:
                    assert reader.get(JobInstance, seed["job_id"]).disarm_guard["job_deadline"] == original_deadline
        if entry == "running":
            changed = recycler._mark_running_timeout(candidate_db, candidate, now, "stale-transition")
        elif entry == "patrol":
            changed = recycler._mark_patrol_stall(candidate_db, candidate, now, interval_seconds=60, age_seconds=7200, reason="stale-transition")
        else:
            changed = recycler._mark_step_trace_stall(candidate_db, candidate, now, last_ts=now-timedelta(hours=2), event_type="COMPLETED", reason="stale-transition")
        # Protected queue/execute and terminal transitions are reread under lock.
        assert changed is False
        candidate_db.rollback()
    with SessionLocal() as verify:
        assert verify.get(JobInstance, seed["job_id"]).status == ("COMPLETED" if transition == "terminal" else "RUNNING")


def test_old_agent_execute_uses_v10_but_waiting_has_no_queue_exemption(client, seed):
    now = datetime.now(timezone.utc)
    with SessionLocal() as writer:
        job = writer.get(JobInstance, seed["job_id"])
        job.execution_state, job.last_progress_at = "EXECUTING_DISARM", now
        job.last_execution_heartbeat_at = now
        writer.commit()
    assert not run_reaper(seed, now)
    with SessionLocal() as writer:
        job = writer.get(JobInstance, seed["job_id"])
        assert job.disarm_guard is None
        job.execution_state = "WAITING_EXECUTION_SLOT"
        writer.commit()
    assert run_reaper(seed, now)


@pytest.mark.parametrize("broken", ["old_token", "other_job_owns_device", "ordinary_waiting"])
def test_phase_evidence_cannot_invent_ownership_or_waiting_protection(client, seed, broken):
    with SessionLocal() as writer:
        if broken == "old_token":
            writer.query(DeviceLease).filter_by(job_id=seed["job_id"]).update({"fencing_token": "rotated-test-token"})
        elif broken == "other_job_owns_device":
            # No fictitious FK: a distinct real job owns the active lease.
            parent = writer.get(JobInstance, seed["job_id"])
            other_run = PlanRun(plan_id=parent.plan_id, status="RUNNING", run_type="MANUAL", triggered_by="test", plan_snapshot={})
            writer.add(other_run)
            writer.flush()
            other_run_id = other_run.id
            other = JobInstance(plan_id=parent.plan_id, plan_run_id=other_run_id, device_id=parent.device_id,
                                host_id=parent.host_id, pipeline_def=PIPE, status="COMPLETED")
            writer.add(other)
            writer.flush()
            writer.query(DeviceLease).filter_by(job_id=seed["job_id"]).update({"job_id": other.id})
            other_id = other.id
        writer.commit()
    if broken != "ordinary_waiting":
        assert heartbeat(client, seed).status_code == 200
    try:
        with SessionLocal() as verify:
            job = verify.get(JobInstance, seed["job_id"])
            assert job.disarm_guard is None
        assert run_reaper(seed, datetime.now(timezone.utc))
    finally:
        if broken == "other_job_owns_device":
            with SessionLocal() as cleanup:
                cleanup.query(DeviceLease).filter_by(job_id=other_id).delete()
                cleanup.query(JobInstance).filter_by(id=other_id).delete()
                cleanup.query(PlanRun).filter_by(id=other_run_id).delete()
                cleanup.commit()


def test_projection_rereads_guard_and_terminal_from_independent_commit(client, seed):
    from backend.services.device_return import job_guard_deadline
    with SessionLocal() as reader:
        stale = reader.get(JobInstance, seed["job_id"])
        assert stale.disarm_guard is None
        assert heartbeat(client, seed).status_code == 200
        deadline = running_heartbeat_deadline(stale, db=reader)
        assert deadline == job_guard_deadline(reader, stale, datetime.now(timezone.utc))
        with SessionLocal() as writer:
            writer.get(JobInstance, seed["job_id"]).status = "COMPLETED"
            writer.commit()
        assert running_heartbeat_deadline(stale, db=reader) is None


def test_eleven_jobs_same_host_queue_6600_then_execute_under_one_permit(client, seed, monkeypatch):
    """Bounded virtual clock, real coordinator/PG/reaper/permit paths."""
    from backend.agent.operation_scheduler import OperationScheduler
    from backend.models.host import Device
    from backend.services import agent_coordinator_heartbeat as heartbeat_service
    origin = datetime.now(timezone.utc)
    clock = [origin]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]
    monkeypatch.setattr(heartbeat_service, "datetime", Clock)
    jobs = [(seed["job_id"], seed["device_id"], "u8-test-token")]
    with SessionLocal() as db:
        db.query(DeviceLease).filter_by(job_id=seed["job_id"]).update({"expires_at": origin+timedelta(hours=3)})
        for i in range(10):
            device = Device(serial=f'u8-multi-{seed["host_id"]}-{i}', host_id=seed["host_id"], status="ONLINE", lease_generation=1)
            db.add(device)
            db.flush()
            job = JobInstance(plan_id=seed["plan_id"], plan_run_id=seed["plan_run_id"], device_id=device.id,
                              host_id=seed["host_id"], status="RUNNING", pipeline_def=PIPE, started_at=origin,
                              execution_state="WAITING_EXECUTION_SLOT")
            db.add(job)
            db.flush()
            token = f"u8-test-token-{i}"
            db.add(DeviceLease(device_id=device.id, job_id=job.id, host_id=seed["host_id"], lease_type="JOB", status="ACTIVE",
                               fencing_token=token, lease_generation=1, agent_instance_id="u8-test-instance", expires_at=origin+timedelta(hours=3)))
            jobs.append((job.id, device.id, token))
        db.commit()
    def send(rows, seq, phase="queue"):
        payload_jobs = [{"job_id": jid, "execution_state": "EXECUTING_DISARM" if phase=="execute" else "WAITING_EXECUTION_SLOT",
                         "disarm_evidence": {"protocol": 1, "step_id": "disarm", "phase": phase, "seq": seq,
                         "progress_seq": 1 if phase=="execute" else 0, "fencing_token": token,
                         "script_name": "powercycle_disarm", "script_version": "1.0.8",
                         "package_sha256": manifest_package_sha("powercycle_disarm", "1.0.8")}} for jid, _, token in rows]
        response = client.post("/api/v1/agent/coordinator-heartbeat", json={
            "host_id": seed["host_id"], "agent_instance_id": "u8-test-instance", "plan_run_hosts": [], "jobs": payload_jobs})
        assert response.status_code == 200, response.text
        assert len(response.json()["data"]["disarm_guards"]) == len(rows)
    try:
        for seq, seconds in enumerate([0, 30, 61, *range(91, 6600, 30), 6600], 1):
            clock[0] = origin+timedelta(seconds=seconds)
            with SessionLocal() as writer:
                writer.get(Host, seed["host_id"]).last_heartbeat = clock[0]
                writer.commit()
            send(jobs, seq)
            if seconds in (61, 6600):
                assert all(not run_reaper({**seed, "job_id": jid}, clock[0]) for jid, _, _ in jobs)
        scheduler = OperationScheduler(max_concurrent=1)
        for jid, did, token in jobs:
            permit = scheduler.acquire(did, timeout=0.1)
            try:
                send([(jid, did, token)], 1000, "execute")
                with SessionLocal() as verify:
                    guard = verify.get(JobInstance, jid).disarm_guard
                    assert guard["step_deadlines"]["disarm"]-clock[0].timestamp() == 660
                    assert guard["job_deadline"] >= guard["step_deadlines"]["disarm"]
            finally:
                permit.release()
    finally:
        with SessionLocal() as cleanup:
            ids = [jid for jid, _, _ in jobs[1:]]
            cleanup.query(DeviceLease).filter(DeviceLease.job_id.in_(ids)).delete(synchronize_session=False)
            cleanup.query(JobInstance).filter(JobInstance.id.in_(ids)).delete(synchronize_session=False)
            cleanup.query(Device).filter(Device.id.in_([did for _, did, _ in jobs[1:]])).delete(synchronize_session=False)
            cleanup.commit()


def test_guard_overrides_late_patrol_state_at_final_write(client, seed):
    """A late generic state report cannot erase fresh committed phase proof."""
    assert heartbeat(client, seed).status_code == 200
    now = datetime.now(timezone.utc)
    with SessionLocal() as writer:
        job = writer.get(JobInstance, seed["job_id"])
        job.execution_state = "PATROL_SLEEP"
        job.last_patrol_heartbeat_at = now-timedelta(hours=2)
        writer.commit()
    with SessionLocal() as db:
        job = db.get(JobInstance, seed["job_id"])
        assert recycler._mark_patrol_stall(db, job, now, interval_seconds=60,
                                          age_seconds=7200, reason="late-generic-state") is False
        db.commit()
    with SessionLocal() as verify:
        assert verify.get(JobInstance, seed["job_id"]).status == "RUNNING"


def test_reaper_uses_lock_time_after_new_phase_receipt(client, seed):
    candidate_time = datetime.now(timezone.utc)-timedelta(seconds=1)
    assert heartbeat(client, seed, phase="execute", progress=1).status_code == 200
    assert run_reaper(seed, candidate_time) is False
    with SessionLocal() as verify:
        job = verify.get(JobInstance, seed["job_id"])
        assert job.status == "RUNNING"
        assert job.disarm_guard.get("reason") != "clock_regressed"


@pytest.mark.parametrize("broken", ["host", "boot", "lease"])
def test_projection_does_not_show_future_protection_after_owner_failure(client, seed, broken):
    assert heartbeat(client, seed).status_code == 200
    with SessionLocal() as reader:
        stale = reader.get(JobInstance, seed["job_id"])
        with SessionLocal() as writer:
            if broken == "host":
                writer.get(Host, seed["host_id"]).last_heartbeat = datetime.now(timezone.utc)-timedelta(hours=1)
            elif broken == "boot":
                writer.get(Host, seed["host_id"]).boot_id = "new-test-boot"
            else:
                writer.query(DeviceLease).filter_by(job_id=seed["job_id"]).update({"expires_at": datetime.now(timezone.utc)-timedelta(seconds=1)})
            writer.commit()
        projected = running_heartbeat_deadline(stale, db=reader)
        assert projected <= datetime.now(timezone.utc)
