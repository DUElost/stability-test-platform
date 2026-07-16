"""ADR-0026 P1 step 4 — queue pump + admission transaction.

Reviewer acceptance targets:
- pump claim: QUEUED→PRECHECK atomic with attempt/token, priority + aging +
  next_admission_at ordering, SKIP LOCKED semantics
- admission success: all-device jobs + counters + PRECHECK→RUNNING +
  started_at reset in ONE transaction; hosts ADMITTED as a unit
- competition (busy device / wifi pool / unique-index race) → full rollback →
  QUEUED + queue_reason + next_admission_at, enqueued_at preserved, never
  FAILED (invariant ④)
- non-retryable (device deleted / no host / script contract) → FAILED
- multi-host all-or-nothing: one blocked device requeues the WHOLE run
- ownership CAS: stale attempt_id no-ops (reaper won)
- drain-only: flag off → pump still admits existing QUEUED
- enqueue failure → immediate requeue
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import backend.core.admission_queue as admission_queue
from backend.models.device_lease import DeviceLease
from backend.models.enums import DeviceStatus, HostStatus, JobStatus, LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun, PlanRunHost, PlanRunTargetDevice
from backend.models.resource_pool import ResourcePool
from backend.models.script import Script
from backend.services.admission_pump import (
    _FatalAdmission,
    _RetryableAdmission,
    admission_transaction,
    claim_queued_plan_runs,
    fail_plan_run_admission,
    pump_admission_tick,
    requeue_plan_run,
)
from backend.services.plan_dispatcher_sync import prepare_plan_run


@pytest.fixture(autouse=True)
def _v2_enabled(monkeypatch):
    monkeypatch.setenv("STP_PLAN_ADMISSION_QUEUE_ENABLED", "1")
    admission_queue.mark_queue_pump_ready(True)
    yield
    admission_queue.mark_queue_pump_ready(False)


@pytest.fixture
def step4_fixture(db_session):
    plan = Plan(name="aq-step4")
    h1 = Host(id="aq4-h1", hostname="aq4-h1", status=HostStatus.ONLINE.value)
    h2 = Host(id="aq4-h2", hostname="aq4-h2", status=HostStatus.ONLINE.value)
    script = Script(
        name="check_device", script_type="python", version="1.0.0",
        nfs_path="/s/check_device.py", content_sha256="abc",
        default_params={"timeout": 30},
    )
    db_session.add_all([plan, h1, h2, script])
    db_session.flush()
    d1 = Device(serial="aq4-d1", host_id="aq4-h1", status=DeviceStatus.ONLINE.value)
    d2 = Device(serial="aq4-d2", host_id="aq4-h1", status=DeviceStatus.ONLINE.value)
    d3 = Device(serial="aq4-d3", host_id="aq4-h2", status=DeviceStatus.ONLINE.value)
    db_session.add_all([d1, d2, d3])
    db_session.flush()
    db_session.add(PlanStep(
        plan_id=plan.id, step_key="init_check", script_name="check_device",
        script_version="1.0.0", stage="init", sort_order=0,
        timeout_seconds=30, retry=0,
    ))
    db_session.commit()
    return {"plan": plan, "h1": h1, "h2": h2, "d1": d1, "d2": d2, "d3": d3}


def _queued_run(db, f, device_ids) -> PlanRun:
    return prepare_plan_run(
        plan_id=f["plan"].id, device_ids=device_ids,
        triggered_by="pytest", db=db, run_type="MANUAL",
    )


def _attach_lease(db, device_id, host_id):
    now = datetime.now(timezone.utc)
    db.add(DeviceLease(
        device_id=device_id, job_id=None, host_id=host_id,
        lease_type=LeaseType.MAINTENANCE.value, status=LeaseStatus.ACTIVE.value,
        fencing_token=f"t4-{device_id}", lease_generation=1,
        agent_instance_id="pytest", reason="test", holder="pytest",
        acquired_at=now, renewed_at=now, expires_at=now + timedelta(seconds=600),
    ))
    db.commit()


# ── Pump claim ────────────────────────────────────────────────────────────────


class TestPumpClaim:
    def test_claim_flips_to_precheck_with_tokens(self, db_session, step4_fixture):
        f = step4_fixture
        pr = _queued_run(db_session, f, [f["d1"].id])

        claimed = claim_queued_plan_runs(db_session)
        assert [(rid, aid is not None) for rid, aid in claimed] == [(pr.id, True)]

        db_session.refresh(pr)
        assert pr.status == "PRECHECK"
        assert pr.precheck_started_at is not None
        assert pr.admission_attempt_id is not None
        assert pr.admission_token is not None

    def test_claim_respects_next_admission_at(self, db_session, step4_fixture):
        f = step4_fixture
        pr = _queued_run(db_session, f, [f["d1"].id])
        pr.next_admission_at = datetime.now(timezone.utc) + timedelta(seconds=120)
        db_session.commit()

        assert claim_queued_plan_runs(db_session) == []
        db_session.refresh(pr)
        assert pr.status == "QUEUED"

    def test_claim_orders_by_priority_then_fifo(self, db_session, step4_fixture):
        f = step4_fixture
        low_old = _queued_run(db_session, f, [f["d1"].id])
        high_new = _queued_run(db_session, f, [f["d2"].id])
        low_old.enqueued_at = datetime.now(timezone.utc) - timedelta(seconds=60)
        high_new.priority = 10
        db_session.commit()

        claimed = claim_queued_plan_runs(db_session, batch=2)
        assert [rid for rid, _ in claimed] == [high_new.id, low_old.id]

    def test_aging_boost_lifts_starved_run(self, db_session, step4_fixture, monkeypatch):
        """A priority-0 run queued long enough outranks a fresh priority-2 run."""
        import backend.services.admission_pump as pump_mod
        monkeypatch.setattr(pump_mod, "ADMISSION_AGING_STEP_SECONDS", 60)
        monkeypatch.setattr(pump_mod, "ADMISSION_AGING_MAX_BOOST", 5)

        f = step4_fixture
        starved = _queued_run(db_session, f, [f["d1"].id])
        fresh_high = _queued_run(db_session, f, [f["d2"].id])
        starved.enqueued_at = datetime.now(timezone.utc) - timedelta(minutes=10)  # +5 boost
        fresh_high.priority = 2
        db_session.commit()

        claimed = claim_queued_plan_runs(db_session, batch=2)
        assert [rid for rid, _ in claimed] == [starved.id, fresh_high.id]


# ── Admission transaction ─────────────────────────────────────────────────────


class TestAdmissionTransaction:
    def _claim(self, db, pr) -> str:
        claimed = claim_queued_plan_runs(db)
        assert claimed and claimed[0][0] == pr.id
        return claimed[0][1]

    def test_success_admits_all_hosts_as_unit(self, db_session, step4_fixture):
        f = step4_fixture
        pr = _queued_run(db_session, f, [f["d1"].id, f["d2"].id, f["d3"].id])
        original_enqueued_at = pr.enqueued_at
        attempt = self._claim(db_session, pr)

        assert admission_transaction(db_session, pr.id, attempt) is True

        db_session.expire_all()
        pr = db_session.get(PlanRun, pr.id)
        assert pr.status == "RUNNING"
        assert pr.total_job_count == 3
        assert pr.queue_reason is None
        assert pr.precheck_started_at is None
        # started_at reset to admission time (queue wait excluded)
        assert pr.started_at > original_enqueued_at
        assert pr.enqueued_at == original_enqueued_at

        jobs = db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id).all()
        assert len(jobs) == 3
        assert all(j.status == JobStatus.PENDING.value for j in jobs)

        hosts = db_session.query(PlanRunHost).filter(
            PlanRunHost.plan_run_id == pr.id).order_by(PlanRunHost.host_id).all()
        assert [(h.status, h.total_job_count) for h in hosts] == [
            ("ADMITTED", 2), ("ADMITTED", 1),
        ]
        assert all(h.admitted_at is not None for h in hosts)

    def test_busy_device_requeues_whole_run(self, db_session, step4_fixture):
        """All-or-nothing: ONE busy device on h2 requeues the run — h1's two
        free devices must NOT be partially admitted (no jobs, host rows stay
        PENDING_ADMISSION)."""
        f = step4_fixture
        pr = _queued_run(db_session, f, [f["d1"].id, f["d2"].id, f["d3"].id])
        original_enqueued_at = pr.enqueued_at
        attempt = self._claim(db_session, pr)
        _attach_lease(db_session, f["d3"].id, "aq4-h2")

        with pytest.raises(_RetryableAdmission) as exc:
            admission_transaction(db_session, pr.id, attempt)
        assert exc.value.queue_reason == "DEVICE_BUSY"

        # route the exception the way plan_admission_task does
        requeue_plan_run(
            db_session, pr.id, attempt,
            queue_reason=exc.value.queue_reason, blockers=exc.value.blockers,
        )

        db_session.expire_all()
        pr = db_session.get(PlanRun, pr.id)
        assert pr.status == "QUEUED"
        assert pr.queue_reason == "DEVICE_BUSY"
        assert pr.next_admission_at is not None
        assert pr.enqueued_at == original_enqueued_at  # aging basis preserved
        assert pr.admission_attempt_id is None
        # zero partial admission
        assert db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id).count() == 0
        hosts = db_session.query(PlanRunHost).filter(
            PlanRunHost.plan_run_id == pr.id).all()
        assert all(h.status == "PENDING_ADMISSION" for h in hosts)
        assert all(h.admitted_at is None for h in hosts)

    def test_wifi_pool_full_requeues_resource_busy(self, db_session, step4_fixture):
        f = step4_fixture
        # make the plan wifi-dependent with a zero-capacity pool
        db_session.add(Script(
            name="connect_wifi", script_type="shell", version="1.0.0",
            nfs_path="/s/wifi.sh", content_sha256="w", default_params={"ssid": ""},
        ))
        db_session.add(PlanStep(
            plan_id=f["plan"].id, step_key="wifi", script_name="connect_wifi",
            script_version="1.0.0", stage="init", sort_order=1,
            timeout_seconds=30, retry=0,
        ))
        db_session.add(ResourcePool(
            name="tiny", resource_type="wifi", config={"ssid": "x", "password": ""},
            max_concurrent_devices=0, is_active=True,
        ))
        db_session.commit()

        pr = _queued_run(db_session, f, [f["d1"].id])
        attempt = self._claim(db_session, pr)

        with pytest.raises(_RetryableAdmission) as exc:
            admission_transaction(db_session, pr.id, attempt)
        assert exc.value.queue_reason == "RESOURCE_BUSY"

        requeue_plan_run(
            db_session, pr.id, attempt,
            queue_reason=exc.value.queue_reason, blockers=exc.value.blockers,
        )
        db_session.expire_all()
        pr = db_session.get(PlanRun, pr.id)
        assert pr.status == "QUEUED"
        assert pr.queue_reason == "RESOURCE_BUSY"
        assert db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id).count() == 0

    def test_unique_index_race_requeues(self, db_session, step4_fixture):
        """Concurrent legacy materialization between re-verify and INSERT →
        uq_job_active_per_device IntegrityError → requeue, never FAILED."""
        f = step4_fixture
        pr = _queued_run(db_session, f, [f["d1"].id])
        attempt = self._claim(db_session, pr)

        # Competitor PlanRun committed up-front (visible to the race session).
        other = PlanRun(
            plan_id=f["plan"].id, status="RUNNING", failure_threshold=0.05,
            plan_snapshot={}, run_type="MANUAL",
        )
        db_session.add(other)
        db_session.commit()
        other_id, plan_id, dev_id = other.id, f["plan"].id, f["d1"].id

        def classify_then_inject(db, device_ids):
            from backend.services.plan_dispatcher_sync import (
                _classify_dispatch_devices_sync as impl,
            )
            result = impl(db, device_ids)
            # competitor claims the device AFTER our re-verify, BEFORE our INSERT
            from backend.core.database import SessionLocal
            with SessionLocal() as race_db:
                race_db.add(JobInstance(
                    plan_run_id=other_id, plan_id=plan_id, device_id=dev_id,
                    host_id="aq4-h1", status=JobStatus.PENDING.value,
                    pipeline_def={"lifecycle": {"init": [], "teardown": []}},
                ))
                race_db.commit()
            return result

        with patch(
            "backend.services.admission_pump._classify_dispatch_devices_sync",
            side_effect=classify_then_inject,
        ):
            with pytest.raises(_RetryableAdmission) as exc:
                admission_transaction(db_session, pr.id, attempt)
        assert exc.value.queue_reason == "DEVICE_BUSY"
        assert exc.value.blockers[0]["reason"] == "device_claimed_concurrently"

    def test_device_unassigned_from_host_fails_fatal(self, db_session, step4_fixture):
        """Device pulled off its host while queued → no_host is a config error
        (fatal), not a scheduling wait. (Hard-deleting a queued device is
        impossible by design — the target snapshot row FK-protects it.)"""
        f = step4_fixture
        pr = _queued_run(db_session, f, [f["d2"].id])
        attempt = self._claim(db_session, pr)

        db_session.query(Device).filter(Device.id == f["d2"].id).update(
            {"host_id": None}
        )
        db_session.commit()

        with pytest.raises(_FatalAdmission) as exc:
            admission_transaction(db_session, pr.id, attempt)
        assert exc.value.reason == "devices_unavailable_at_admission"

        fail_plan_run_admission(
            db_session, pr.id, attempt,
            reason=exc.value.reason, detail=exc.value.detail,
        )
        db_session.expire_all()
        pr = db_session.get(PlanRun, pr.id)
        assert pr.status == "FAILED"
        assert pr.result_summary["reason"] == "devices_unavailable_at_admission"

    def test_stale_attempt_noops(self, db_session, step4_fixture):
        """Ownership CAS: if the reaper already recovered the run (attempt
        rotated), an old admission attempt must not touch it."""
        f = step4_fixture
        pr = _queued_run(db_session, f, [f["d1"].id])
        attempt = self._claim(db_session, pr)

        # reaper-style recovery rotates ownership
        requeue_plan_run(
            db_session, pr.id, attempt,
            queue_reason="PRECHECK_STALE", blockers=None, backoff_seconds=0,
        )
        db_session.expire_all()

        assert admission_transaction(db_session, pr.id, attempt) is False
        pr = db_session.get(PlanRun, pr.id)
        assert pr.status == "QUEUED"
        assert db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id).count() == 0


# ── Pump tick end-to-end (enqueue mocked) ─────────────────────────────────────


class TestPumpTick:
    def test_tick_claims_and_enqueues(self, db_session, step4_fixture):
        f = step4_fixture
        pr = _queued_run(db_session, f, [f["d1"].id])

        with patch(
            "backend.tasks.saq_worker.enqueue_sync", return_value=True,
        ) as mock_enq:
            summary = pump_admission_tick()

        assert summary["claimed"] == 1
        assert summary["enqueued"] == 1
        mock_enq.assert_called_once()
        kwargs = mock_enq.call_args.kwargs
        assert kwargs["plan_run_id"] == pr.id
        assert kwargs["attempt_id"]

        db_session.expire_all()
        assert db_session.get(PlanRun, pr.id).status == "PRECHECK"

    def test_tick_enqueue_failure_requeues_immediately(self, db_session, step4_fixture):
        f = step4_fixture
        pr = _queued_run(db_session, f, [f["d1"].id])

        with patch(
            "backend.tasks.saq_worker.enqueue_sync", return_value=False,
        ):
            summary = pump_admission_tick()

        assert summary["requeued_on_enqueue_failure"] == 1
        db_session.expire_all()
        pr = db_session.get(PlanRun, pr.id)
        assert pr.status == "QUEUED"
        assert pr.queue_reason == "PRECHECK_STALE"
        assert pr.next_admission_at is not None

    def test_drain_only_admits_with_flag_off(self, db_session, step4_fixture, monkeypatch):
        """Reviewer boundary #5: flag off must NOT strand existing QUEUED —
        the pump keeps draining them."""
        f = step4_fixture
        pr = _queued_run(db_session, f, [f["d1"].id])  # created while flag on

        monkeypatch.delenv("STP_PLAN_ADMISSION_QUEUE_ENABLED", raising=False)

        with patch(
            "backend.tasks.saq_worker.enqueue_sync", return_value=True,
        ) as mock_enq:
            summary = pump_admission_tick()

        assert summary["claimed"] == 1
        mock_enq.assert_called_once()
        db_session.expire_all()
        assert db_session.get(PlanRun, pr.id).status == "PRECHECK"

    def test_tick_noop_when_queue_empty(self, db_session):
        with patch("backend.tasks.saq_worker.enqueue_sync") as mock_enq:
            summary = pump_admission_tick()
        assert summary == {"claimed": 0, "enqueued": 0, "requeued_on_enqueue_failure": 0}
        mock_enq.assert_not_called()


# ── Full async task path (verify mocked) ──────────────────────────────────────


class TestAdmissionTaskEndToEnd:
    def test_verify_ok_then_running(self, db_session, step4_fixture):
        import asyncio
        from backend.services.admission_pump import plan_admission_task

        f = step4_fixture
        pr = _queued_run(db_session, f, [f["d1"].id, f["d3"].id])
        claimed = claim_queued_plan_runs(db_session)
        attempt = claimed[0][1]
        db_session.expire_all()

        async def fake_gather(host_ids, expected):
            return {hid: (True, [{"ok": True}], None) for hid in host_ids}

        with patch(
            "backend.services.precheck.verify.gather_verify", new=fake_gather,
        ):
            asyncio.run(plan_admission_task({}, plan_run_id=pr.id, attempt_id=attempt))

        db_session.expire_all()
        pr = db_session.get(PlanRun, pr.id)
        assert pr.status == "RUNNING"
        assert db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id).count() == 2

    def test_agent_offline_requeues(self, db_session, step4_fixture):
        import asyncio
        from backend.services.admission_pump import plan_admission_task

        f = step4_fixture
        pr = _queued_run(db_session, f, [f["d1"].id])
        claimed = claim_queued_plan_runs(db_session)
        attempt = claimed[0][1]
        db_session.expire_all()

        async def fake_gather(host_ids, expected):
            return {hid: (False, [], "agent_offline") for hid in host_ids}

        with patch(
            "backend.services.precheck.verify.gather_verify", new=fake_gather,
        ):
            asyncio.run(plan_admission_task({}, plan_run_id=pr.id, attempt_id=attempt))

        db_session.expire_all()
        pr = db_session.get(PlanRun, pr.id)
        assert pr.status == "QUEUED"
        assert pr.queue_reason == "DEVICE_BUSY"
        assert db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id).count() == 0

    def test_script_mismatch_after_sync_fails_fatal(self, db_session, step4_fixture):
        import asyncio
        from backend.services.admission_pump import plan_admission_task

        f = step4_fixture
        pr = _queued_run(db_session, f, [f["d1"].id])
        claimed = claim_queued_plan_runs(db_session)
        attempt = claimed[0][1]
        db_session.expire_all()

        async def fake_gather(host_ids, expected):
            return {
                hid: (False, [{"ok": False, "name": "check_device"}], "sha_mismatch")
                for hid in host_ids
            }

        with patch(
            "backend.services.precheck.verify.gather_verify", new=fake_gather,
        ), patch(
            "backend.services.precheck.sync.push_mismatched_scripts",
            return_value=(False, "no_ssh_credentials"),
        ):
            asyncio.run(plan_admission_task({}, plan_run_id=pr.id, attempt_id=attempt))

        db_session.expire_all()
        pr = db_session.get(PlanRun, pr.id)
        assert pr.status == "FAILED"
        assert pr.result_summary["reason"] == "script_verify_failed"
