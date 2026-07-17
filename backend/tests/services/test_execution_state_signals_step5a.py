"""ADR-0026 P1 step 5a — three-signal ingestion + sub-state timeout clocks.

Control-plane half (Agent-side production of these signals lands in 5b):
- extend-batch persists execution_state / last_execution_heartbeat_at /
  last_progress_at for renewed items (invariant ③ — one round-trip, three
  independent signals)
- recycler selects the liveness clock per execution_state (§3 matrix):
  EXECUTING_STEP → executor heartbeat; WAITING_*/PATROL_SLEEP → per-host
  coordinator heartbeat; NULL/missing signals → legacy updated_at fallback
- recovery payload carries execution_state (frozen resume contract)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.core.database import SessionLocal
from backend.models.device_lease import DeviceLease
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun, PlanRunHost
from backend.scheduler.recycler import (
    COORDINATOR_HEARTBEAT_TIMEOUT_SECONDS,
    _running_liveness_anchor,
    recycle_once,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def signal_fixture(db_session):
    suffix = uuid4().hex[:6]
    plan = Plan(name=f"sig5a-{suffix}")
    host = Host(id=f"sig5a-h-{suffix}", hostname="sig5a", status=HostStatus.ONLINE.value)
    db_session.add_all([plan, host])
    db_session.flush()
    dev = Device(serial=f"sig5a-d-{suffix}", host_id=host.id, status="ONLINE")
    db_session.add(dev)
    db_session.flush()
    pr = PlanRun(
        plan_id=plan.id, status="RUNNING", failure_threshold=0.05,
        plan_snapshot={}, run_type="MANUAL",
    )
    db_session.add(pr)
    db_session.flush()
    job = JobInstance(
        plan_run_id=pr.id, plan_id=plan.id, device_id=dev.id,
        host_id=host.id, status=JobStatus.RUNNING.value,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    db_session.flush()
    now = datetime.now(timezone.utc)
    token = f"{dev.id}:1"
    db_session.add(DeviceLease(
        device_id=dev.id, job_id=job.id, host_id=host.id,
        lease_type=LeaseType.JOB.value, status=LeaseStatus.ACTIVE.value,
        fencing_token=token, lease_generation=1,
        agent_instance_id=host.id,
        acquired_at=now, renewed_at=now, expires_at=now + timedelta(seconds=600),
    ))
    db_session.commit()
    return {"plan": plan, "host": host, "device": dev, "run": pr, "job": job, "token": token}


# ── extend-batch three-signal persistence ─────────────────────────────────────


class TestBatchRenewalSignalIngestion:
    async def _renew(self, fixture, *, execution_state=None, progress_marker=None):
        from backend.api.routes.agent_api import (
            _ExtendBatchIn, _ExtendBatchItemIn, extend_leases_batch,
        )
        from backend.core.database import AsyncSessionLocal, async_engine

        await async_engine.dispose()
        async with AsyncSessionLocal() as adb:
            return await extend_leases_batch(
                payload=_ExtendBatchIn(
                    host_id=fixture["host"].id,
                    agent_instance_id=fixture["host"].id,
                    leases=[_ExtendBatchItemIn(
                        job_id=fixture["job"].id,
                        fencing_token=fixture["token"],
                        execution_state=execution_state,
                        progress_marker=progress_marker,
                    )],
                ),
                db=adb, _=None,
            )

    @pytest.mark.asyncio
    async def test_renewal_persists_three_signals(self, db_session, signal_fixture):
        progress_ts = "2026-07-16T08:00:00+00:00"
        result = await self._renew(
            signal_fixture,
            execution_state="PATROL_SLEEP",
            progress_marker={"patrol_cycle_index": 7, "last_progress_at": progress_ts},
        )
        assert result.data.results[0].status == "renewed"

        db = SessionLocal()
        try:
            job = db.get(JobInstance, signal_fixture["job"].id)
            assert job.execution_state == "PATROL_SLEEP"
            assert job.last_execution_heartbeat_at is not None
            assert job.last_progress_at is not None
            assert job.last_progress_at.isoformat().startswith("2026-07-16T08:00:00")
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_invalid_execution_state_ignored(self, db_session, signal_fixture):
        result = await self._renew(signal_fixture, execution_state="DANCING")
        assert result.data.results[0].status == "renewed"

        db = SessionLocal()
        try:
            job = db.get(JobInstance, signal_fixture["job"].id)
            assert job.execution_state is None  # unknown value never persisted
            assert job.last_execution_heartbeat_at is not None  # arrival proof still counts
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_legacy_agent_without_signals_still_renews(self, db_session, signal_fixture):
        result = await self._renew(signal_fixture)
        assert result.data.results[0].status == "renewed"

        db = SessionLocal()
        try:
            job = db.get(JobInstance, signal_fixture["job"].id)
            assert job.execution_state is None
            assert job.last_progress_at is None
            assert job.last_execution_heartbeat_at is not None
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_garbage_progress_marker_ignored(self, db_session, signal_fixture):
        result = await self._renew(
            signal_fixture,
            execution_state="EXECUTING_STEP",
            progress_marker={"last_progress_at": "not-a-timestamp"},
        )
        assert result.data.results[0].status == "renewed"
        db = SessionLocal()
        try:
            job = db.get(JobInstance, signal_fixture["job"].id)
            assert job.execution_state == "EXECUTING_STEP"
            assert job.last_progress_at is None
        finally:
            db.close()


# ── recycler sub-state clock selection ────────────────────────────────────────


class TestRunningLivenessAnchor:
    def _job(self, **kw):
        from types import SimpleNamespace
        base = dict(
            plan_run_id=1, host_id="h", execution_state=None,
            last_execution_heartbeat_at=None, updated_at=None,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
            last_patrol_heartbeat_at=None,
        )
        base.update(kw)
        return SimpleNamespace(**base)

    def test_executing_step_uses_executor_heartbeat(self):
        hb = datetime.now(timezone.utc) - timedelta(seconds=10)
        stale = datetime.now(timezone.utc) - timedelta(hours=2)
        job = self._job(
            execution_state="EXECUTING_STEP",
            last_execution_heartbeat_at=hb, updated_at=stale,
        )
        anchor, _timeout = _running_liveness_anchor(job, {})
        assert anchor == hb  # NOT the stale updated_at

    def test_waiting_uses_coordinator_heartbeat(self):
        coord_hb = datetime.now(timezone.utc) - timedelta(seconds=5)
        stale = datetime.now(timezone.utc) - timedelta(hours=2)
        job = self._job(execution_state="WAITING_EXECUTION_SLOT", updated_at=stale)
        anchor, timeout = _running_liveness_anchor(job, {(1, "h"): coord_hb})
        assert anchor == coord_hb
        assert timeout == COORDINATOR_HEARTBEAT_TIMEOUT_SECONDS

    def test_waiting_without_coordinator_falls_back_to_updated_at(self):
        stale = datetime.now(timezone.utc) - timedelta(hours=2)
        job = self._job(execution_state="PATROL_SLEEP", updated_at=stale)
        anchor, _timeout = _running_liveness_anchor(job, {})
        assert anchor == stale  # legacy clock

    def test_null_execution_state_is_pure_legacy(self):
        ts = datetime.now(timezone.utc) - timedelta(seconds=30)
        job = self._job(updated_at=ts)
        anchor, _timeout = _running_liveness_anchor(job, {})
        assert anchor == ts


class TestRecyclerSubStateClocks:
    def _stale(self, seconds=7200):
        return datetime.now(timezone.utc) - timedelta(seconds=seconds)

    def test_waiting_job_with_fresh_coordinator_survives(
        self, db_session, signal_fixture,
    ):
        """Invariant ②: a WAITING job whose per-host coordinator is alive must
        NOT be recycled even while its updated_at is long stale."""
        f = signal_fixture
        job = f["job"]
        job.execution_state = "WAITING_EXECUTION_SLOT"
        job.updated_at = self._stale()
        db_session.add(PlanRunHost(
            plan_run_id=f["run"].id, host_id=f["host"].id,
            coordinator_heartbeat_at=datetime.now(timezone.utc),
        ))
        db_session.commit()

        recycle_once()

        db_session.expire_all()
        assert db_session.get(JobInstance, job.id).status == "RUNNING"

    def test_waiting_job_with_stale_coordinator_goes_unknown(
        self, db_session, signal_fixture,
    ):
        f = signal_fixture
        job = f["job"]
        job.execution_state = "WAITING_EXECUTION_SLOT"
        job.updated_at = self._stale()
        db_session.add(PlanRunHost(
            plan_run_id=f["run"].id, host_id=f["host"].id,
            coordinator_heartbeat_at=self._stale(
                COORDINATOR_HEARTBEAT_TIMEOUT_SECONDS + 60
            ),
        ))
        db_session.commit()

        recycle_once()

        db_session.expire_all()
        assert db_session.get(JobInstance, job.id).status == "UNKNOWN"

    def test_executing_job_with_fresh_exec_heartbeat_survives(
        self, db_session, signal_fixture,
    ):
        f = signal_fixture
        job = f["job"]
        job.execution_state = "EXECUTING_STEP"
        job.updated_at = self._stale()
        job.last_execution_heartbeat_at = datetime.now(timezone.utc)
        db_session.commit()

        recycle_once()

        db_session.expire_all()
        assert db_session.get(JobInstance, job.id).status == "RUNNING"

    def test_legacy_job_stale_updated_at_goes_unknown(self, db_session, signal_fixture):
        """NULL execution_state (legacy agent) → old rule byte-for-byte."""
        f = signal_fixture
        job = f["job"]
        job.updated_at = self._stale()
        db_session.commit()

        recycle_once()

        db_session.expire_all()
        assert db_session.get(JobInstance, job.id).status == "UNKNOWN"


# ── recovery payload carries execution_state ──────────────────────────────────


@pytest.mark.asyncio
async def test_recovery_payload_includes_execution_state(db_session, signal_fixture):
    from backend.api.routes.agent_api import _build_recovery_job_payload
    from backend.core.database import AsyncSessionLocal, async_engine

    f = signal_fixture
    f["job"].execution_state = "PATROL_SLEEP"
    db_session.commit()

    await async_engine.dispose()
    async with AsyncSessionLocal() as adb:
        job = await adb.get(JobInstance, f["job"].id)
        payload = await _build_recovery_job_payload(
            adb, job,
            device_serial=f["device"].serial,
            fencing_token=f["token"],
        )
    assert payload["execution_state"] == "PATROL_SLEEP"
