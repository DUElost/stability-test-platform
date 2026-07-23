"""ADR-0026 P1 step 3 — V2 prepare snapshot (sole dispatch path).

Reviewer boundaries covered:
1. Validation semantics — fatal (not_found/no_host/config) reject 400;
   retryable (busy/offline) create QUEUED with queue_reason.
2. V2 never drives the legacy gate — MANUAL / SCHEDULE / CHAIN return
   QUEUED; the pump owns admission.
3. Atomic snapshot: PlanRun + PlanRunHost + PlanRunTargetDevice in one
   transaction; duplicate device ids deduplicated.
4. Deterministic ordering (sorted device ids → sort_order) and time
   semantics (enqueued_at set once; started_at holds a compat value).
5. flag=0 or pump-not-ready rejects prepare (no legacy RUNNING fallback).
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
from backend.models.script import Script
from backend.services.plan_dispatcher_core import PlanDispatchError
from backend.services.plan_dispatcher_sync import dispatch_plan_sync, prepare_plan_run


@pytest.fixture(autouse=True)
def _v2_enabled(monkeypatch):
    """Default every test in this module to V2 ON (flag + pump ready).

    Individual tests may flip it off via mark_queue_pump_ready(False) /
    delenv to exercise the legacy branch.
    """
    monkeypatch.setenv("STP_PLAN_ADMISSION_QUEUE_ENABLED", "1")
    admission_queue.mark_queue_pump_ready(True)
    yield
    admission_queue.mark_queue_pump_ready(False)


@pytest.fixture
def step3_fixture(db_session):
    plan = Plan(name="aq-step3")
    h1 = Host(id="aq3-h1", hostname="aq3-h1", status=HostStatus.ONLINE.value)
    h2 = Host(id="aq3-h2", hostname="aq3-h2", status=HostStatus.ONLINE.value)
    script = Script(
        name="check_device", script_type="python", version="1.0.0",
        nfs_path="/s/check_device.py", content_sha256="abc",
        default_params={"timeout": 30},
    )
    db_session.add_all([plan, h1, h2, script])
    db_session.flush()
    d1 = Device(serial="aq3-d1", host_id="aq3-h1", status=DeviceStatus.ONLINE.value)
    d2 = Device(serial="aq3-d2", host_id="aq3-h1", status=DeviceStatus.ONLINE.value)
    d3 = Device(serial="aq3-d3", host_id="aq3-h2", status=DeviceStatus.ONLINE.value)
    db_session.add_all([d1, d2, d3])
    db_session.flush()
    db_session.add(PlanStep(
        plan_id=plan.id, step_key="init_check", script_name="check_device",
        script_version="1.0.0", stage="init", sort_order=0,
        timeout_seconds=30, retry=0,
    ))
    db_session.commit()
    return {"plan": plan, "h1": h1, "h2": h2, "d1": d1, "d2": d2, "d3": d3}


def _attach_lease(db, device_id, host_id):
    now = datetime.now(timezone.utc)
    db.add(DeviceLease(
        device_id=device_id, job_id=None, host_id=host_id,
        lease_type=LeaseType.MAINTENANCE.value, status=LeaseStatus.ACTIVE.value,
        fencing_token=f"t-{device_id}", lease_generation=1,
        agent_instance_id="pytest", reason="test", holder="pytest",
        acquired_at=now, renewed_at=now, expires_at=now + timedelta(seconds=600),
    ))
    db.commit()


# ── Boundary 1+3+4: QUEUED creation with atomic ordered snapshot ─────────────


class TestQueuedPrepare:
    def test_clean_devices_create_queued_with_snapshot(self, db_session, step3_fixture):
        f = step3_fixture
        pr = prepare_plan_run(
            plan_id=f["plan"].id,
            device_ids=[f["d3"].id, f["d1"].id, f["d2"].id],  # unsorted input
            triggered_by="pytest", db=db_session, run_type="MANUAL",
        )
        assert pr.status == "QUEUED"
        assert pr.enqueued_at is not None
        assert pr.queue_reason is None
        assert pr.started_at is not None  # NOT-NULL compat value until admission

        # No jobs (invariant ①), no legacy gate side effects
        assert db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id).count() == 0

        # Host groups: sorted, correct device_count
        hosts = db_session.query(PlanRunHost).filter(
            PlanRunHost.plan_run_id == pr.id).order_by(PlanRunHost.host_id).all()
        assert [(h.host_id, h.device_count) for h in hosts] == [
            ("aq3-h1", 2), ("aq3-h2", 1),
        ]
        assert all(h.status == "PENDING_ADMISSION" for h in hosts)

        # Target rows: deduped, sorted by device_id, sort_order repeatable,
        # host_id_snapshot matches, bound to the right host group
        targets = db_session.query(PlanRunTargetDevice).filter(
            PlanRunTargetDevice.plan_run_id == pr.id
        ).order_by(PlanRunTargetDevice.sort_order).all()
        expected_ids = sorted([f["d1"].id, f["d2"].id, f["d3"].id])
        assert [t.device_id for t in targets] == expected_ids
        assert [t.sort_order for t in targets] == [0, 1, 2]
        host_by_id = {h.id: h.host_id for h in hosts}
        for t in targets:
            assert t.host_id_snapshot == host_by_id[t.plan_run_host_id]

        # Compat read path mirrors the deduped sorted list
        assert pr.run_context["dispatch_device_ids"] == expected_ids

    def test_duplicate_device_ids_deduplicated(self, db_session, step3_fixture):
        f = step3_fixture
        pr = prepare_plan_run(
            plan_id=f["plan"].id,
            device_ids=[f["d1"].id, f["d1"].id, f["d2"].id],
            triggered_by="pytest", db=db_session, run_type="MANUAL",
        )
        targets = db_session.query(PlanRunTargetDevice).filter(
            PlanRunTargetDevice.plan_run_id == pr.id).all()
        assert len(targets) == 2

    def test_caller_prefilled_device_ids_overwritten(self, db_session, step3_fixture):
        """Contract: V2 force-writes the canonicalized list — a caller-prefilled
        run_context.dispatch_device_ids must never diverge from the snapshot."""
        f = step3_fixture
        pr = prepare_plan_run(
            plan_id=f["plan"].id,
            device_ids=[f["d2"].id, f["d1"].id],
            triggered_by="pytest", db=db_session, run_type="MANUAL",
            run_context={"dispatch_device_ids": [999999]},  # stale prefill
        )
        assert pr.run_context["dispatch_device_ids"] == sorted([f["d1"].id, f["d2"].id])

    def test_mid_snapshot_insert_failure_leaves_no_residue(
        self, db_session, step3_fixture,
    ):
        """Reviewer contract: any Host/Target insert failure rolls the WHOLE
        PlanRun back — no PlanRun / PlanRunHost / PlanRunTargetDevice rows."""
        f = step3_fixture
        real_cls = PlanRunTargetDevice
        calls = {"n": 0}

        def exploding_target(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:  # fail on the SECOND target row (mid-insert)
                raise RuntimeError("boom-mid-snapshot")
            return real_cls(*args, **kwargs)

        with patch(
            "backend.models.plan_run.PlanRunTargetDevice",
            side_effect=exploding_target,
        ):
            with pytest.raises(RuntimeError, match="boom-mid-snapshot"):
                prepare_plan_run(
                    plan_id=f["plan"].id,
                    device_ids=[f["d1"].id, f["d2"].id],
                    triggered_by="pytest", db=db_session, run_type="MANUAL",
                )
        db_session.rollback()

        assert db_session.query(PlanRun).count() == 0
        assert db_session.query(PlanRunHost).count() == 0
        assert db_session.query(PlanRunTargetDevice).count() == 0

    def test_busy_device_queues_with_reason(self, db_session, step3_fixture):
        """THE cross-PlanRun queuing capability: active lease no longer 400s."""
        f = step3_fixture
        _attach_lease(db_session, f["d1"].id, "aq3-h1")

        pr = prepare_plan_run(
            plan_id=f["plan"].id, device_ids=[f["d1"].id, f["d2"].id],
            triggered_by="pytest", db=db_session, run_type="MANUAL",
        )
        assert pr.status == "QUEUED"
        assert pr.queue_reason == "DEVICE_BUSY"
        blockers = pr.run_context["queue_blockers"]
        assert blockers[0]["id"] == f["d1"].id
        assert blockers[0]["reason"] == "active_lease"
        # Snapshot still covers ALL target devices (busy one included)
        assert db_session.query(PlanRunTargetDevice).filter(
            PlanRunTargetDevice.plan_run_id == pr.id).count() == 2

    def test_active_job_queues(self, db_session, step3_fixture):
        """A PENDING job from another PlanRun queues the new run (was a 400)."""
        f = step3_fixture
        other = PlanRun(
            plan_id=f["plan"].id, status="RUNNING", failure_threshold=0.05,
            plan_snapshot={}, run_type="MANUAL",
        )
        db_session.add(other)
        db_session.flush()
        db_session.add(JobInstance(
            plan_run_id=other.id, plan_id=f["plan"].id, device_id=f["d1"].id,
            host_id="aq3-h1", status=JobStatus.PENDING.value,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        ))
        db_session.commit()

        pr = prepare_plan_run(
            plan_id=f["plan"].id, device_ids=[f["d1"].id],
            triggered_by="pytest", db=db_session, run_type="MANUAL",
        )
        assert pr.status == "QUEUED"
        assert pr.queue_reason == "DEVICE_BUSY"
        assert pr.run_context["queue_blockers"][0]["reason"] == "active_job"

    @pytest.mark.parametrize("mutate,expected_reason", [
        (lambda f, db: setattr(f["d1"], "status", DeviceStatus.OFFLINE.value), "device_offline"),
        (lambda f, db: setattr(f["d1"], "status", DeviceStatus.ERROR.value), "device_error"),
        (lambda f, db: setattr(f["h1"], "status", HostStatus.OFFLINE.value), "host_offline"),
    ])
    def test_transient_unavailability_queues(
        self, db_session, step3_fixture, mutate, expected_reason,
    ):
        f = step3_fixture
        mutate(f, db_session)
        db_session.commit()

        pr = prepare_plan_run(
            plan_id=f["plan"].id, device_ids=[f["d1"].id],
            triggered_by="pytest", db=db_session, run_type="MANUAL",
        )
        assert pr.status == "QUEUED"
        assert pr.queue_reason == "DEVICE_BUSY"
        assert pr.run_context["queue_blockers"][0]["reason"] == expected_reason

    def test_not_found_still_rejects_400(self, db_session, step3_fixture):
        f = step3_fixture
        baseline = db_session.query(PlanRun).count()
        with pytest.raises(PlanDispatchError) as exc:
            prepare_plan_run(
                plan_id=f["plan"].id, device_ids=[f["d1"].id, 999999],
                triggered_by="pytest", db=db_session, run_type="MANUAL",
            )
        entries = exc.value.detail()["unavailable_devices"]
        # only the fatal entry — a busy/offline device is not an error in V2
        assert [e["reason"] for e in entries] == ["not_found"]
        assert db_session.query(PlanRun).count() == baseline

    def test_no_host_still_rejects_400(self, db_session, step3_fixture):
        f = step3_fixture
        f["d1"].host_id = None
        db_session.commit()
        with pytest.raises(PlanDispatchError) as exc:
            prepare_plan_run(
                plan_id=f["plan"].id, device_ids=[f["d1"].id],
                triggered_by="pytest", db=db_session, run_type="MANUAL",
            )
        assert exc.value.detail()["unavailable_devices"][0]["reason"] == "no_host"

    def test_missing_script_still_rejects_400(self, db_session, step3_fixture):
        f = step3_fixture
        db_session.add(PlanStep(
            plan_id=f["plan"].id, step_key="ghost", script_name="ghost",
            script_version="9.9.9", stage="init", sort_order=1,
            timeout_seconds=30, retry=0,
        ))
        db_session.commit()
        with pytest.raises(PlanDispatchError) as exc:
            prepare_plan_run(
                plan_id=f["plan"].id, device_ids=[f["d1"].id],
                triggered_by="pytest", db=db_session, run_type="MANUAL",
            )
        assert exc.value.detail()["code"] == "INVALID_SCRIPT_REFS"


# ── Boundary 2: V2 never drives the legacy gate ──────────────────────────────


class TestLegacyGateBypass:
    def test_dispatch_plan_sync_returns_queued(self, db_session, step3_fixture):
        f = step3_fixture
        pr = dispatch_plan_sync(
            plan_id=f["plan"].id, device_ids=[f["d1"].id],
            triggered_by="pytest", db=db_session, run_type="SCHEDULE",
        )
        assert pr.status == "QUEUED"

    def test_manual_route_returns_queued(
        self, client, auth_headers, db_session, step3_fixture,
    ):
        f = step3_fixture
        resp = client.post(
            f"/api/v1/plans/{f['plan'].id}/run",
            json={"device_ids": [f["d1"].id]},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["status"] == "QUEUED"

    def test_chain_trigger_returns_queued_child(self, db_session, step3_fixture):
        """CHAIN path: child lands QUEUED with snapshot rows for pump admission."""
        from backend.services.plan_chain_trigger import trigger_next_plan_sync

        f = step3_fixture
        next_plan = Plan(name="aq-step3-next")
        db_session.add(next_plan)
        db_session.flush()
        db_session.add(PlanStep(
            plan_id=next_plan.id, step_key="init_check", script_name="check_device",
            script_version="1.0.0", stage="init", sort_order=0,
            timeout_seconds=30, retry=0,
        ))
        parent = PlanRun(
            plan_id=f["plan"].id, status="SUCCESS", failure_threshold=0.05,
            plan_snapshot={"plan": {"next_plan_id": next_plan.id}},
            run_type="MANUAL",
            ended_at=datetime.now(timezone.utc),
        )
        db_session.add(parent)
        db_session.flush()
        db_session.add(JobInstance(
            plan_run_id=parent.id, plan_id=f["plan"].id, device_id=f["d1"].id,
            host_id="aq3-h1", status=JobStatus.COMPLETED.value,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        ))
        db_session.commit()

        child = trigger_next_plan_sync(parent, db_session)

        assert child is not None
        assert child.status == "QUEUED"
        assert child.run_type == "CHAIN"
        assert child.parent_plan_run_id == parent.id
        assert db_session.query(PlanRunTargetDevice).filter(
            PlanRunTargetDevice.plan_run_id == child.id).count() == 1


# ── Admission prerequisites ───────────────────────────────────────────────────


class TestAdmissionPrerequisites:
    def test_flag_off_rejects_prepare(self, db_session, step3_fixture, monkeypatch):
        monkeypatch.setenv("STP_PLAN_ADMISSION_QUEUE_ENABLED", "0")
        admission_queue.mark_queue_pump_ready(False)

        f = step3_fixture
        with pytest.raises(PlanDispatchError, match="disabled"):
            prepare_plan_run(
                plan_id=f["plan"].id, device_ids=[f["d1"].id],
                triggered_by="pytest", db=db_session, run_type="MANUAL",
            )

    def test_pump_not_ready_rejects_prepare(
        self, db_session, step3_fixture, monkeypatch,
    ):
        monkeypatch.setenv("STP_PLAN_ADMISSION_QUEUE_ENABLED", "1")
        admission_queue.mark_queue_pump_ready(False)

        f = step3_fixture
        with pytest.raises(PlanDispatchError, match="pump is not ready"):
            prepare_plan_run(
                plan_id=f["plan"].id, device_ids=[f["d1"].id],
                triggered_by="pytest", db=db_session, run_type="MANUAL",
            )

    def test_busy_device_queues_when_admission_ready(
        self, db_session, step3_fixture,
    ):
        f = step3_fixture
        _attach_lease(db_session, f["d1"].id, "aq3-h1")
        pr = prepare_plan_run(
            plan_id=f["plan"].id, device_ids=[f["d1"].id],
            triggered_by="pytest", db=db_session, run_type="MANUAL",
        )
        assert pr.status == "QUEUED"
        assert pr.queue_reason is not None
