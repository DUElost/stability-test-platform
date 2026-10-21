"""#3723 U3 / ADR-0061 D5：控制面欠账行为（隔离 PG）。"""

from __future__ import annotations

import os
from backend.tests.disarm_probe_evidence import trust_test_probe
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.models.device_lease import DeviceLease
from backend.models.device_return_debt import DeviceReturnDebt
from backend.models.enums import JobStatus, LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services.device_return import (
    apply_discharge_sync,
    apply_override_sync,
    handle_job_lease_release_sync,
    init_device_return_sync,
    is_debt_lease,
)
from backend.services.device_lease_emergency_release import release_terminal_job_lease
from backend.services.device_retirement import retire_device, unretire_device
from backend.services.errors import Conflict
from backend.services.plan_dispatcher_sync import _classify_dispatch_devices_sync

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
IS_PG = TEST_DATABASE_URL.startswith("postgresql")
pytestmark = pytest.mark.skipif(not IS_PG, reason="device_return_debt unique index requires PostgreSQL")

_NOW = datetime.now(timezone.utc)


def _ensure_open_source_index(db):
    db.execute(text(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_device_return_debt_open_source
        ON device_return_debt (
            device_id, family, source_kind, (COALESCE(source_job_id, 0))
        )
        WHERE state = 'open'
        """
    ))
    db.commit()


def _seed(db, *, disarm_results=None):
    _ensure_open_source_index(db)
    suffix = uuid4().hex[:8]
    host = Host(
        id=f"u3-{suffix}",
        hostname=f"u3-{suffix}",
        status="ONLINE",
        last_heartbeat=_NOW,
        created_at=_NOW,
    )
    device = Device(
        serial=f"U3-{suffix}",
        host_id=host.id,
        status="ONLINE",
        tags=[],
        created_at=_NOW,
        last_seen=_NOW,
        lease_generation=1,
    )
    plan = Plan(name=f"u3-{suffix}", description="u3", created_by="test")
    db.add_all([host, device, plan])
    db.flush()
    run = PlanRun(
        plan_id=plan.id,
        status="RUNNING",
        plan_snapshot={"name": plan.name},
        run_type="MANUAL",
        triggered_by="test",
    )
    db.add(run)
    db.flush()
    if disarm_results is None:
        disarm_results = {"power-cycle": {"result": "not_executed", "reason": "test"}}
    job = JobInstance(
        plan_run_id=run.id,
        plan_id=plan.id,
        device_id=device.id,
        host_id=host.id,
        status=JobStatus.COMPLETED.value,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        ended_at=_NOW,
        disarm_results=disarm_results,
    )
    db.add(job)
    db.flush()
    lease = DeviceLease(
        device_id=device.id,
        job_id=job.id,
        host_id=host.id,
        lease_type=LeaseType.JOB.value,
        status=LeaseStatus.ACTIVE.value,
        fencing_token=f"{device.id}:1",
        lease_generation=1,
        agent_instance_id=f"agent-{suffix}",
        acquired_at=_NOW,
        renewed_at=_NOW,
        expires_at=_NOW + timedelta(hours=1),
    )
    db.add(lease)
    db.commit()
    db.refresh(device)
    db.refresh(job)
    db.refresh(lease)
    return device, job, lease, host


class TestDeviceReturnU3:
    def test_terminal_converts_job_lease_to_debt(self, db_session):
        device, job, lease, _ = _seed(db_session)
        outcome = handle_job_lease_release_sync(
            db_session, device_id=device.id, job_id=job.id, job=job,
        )
        db_session.commit()
        assert outcome.converted_to_debt is True
        assert outcome.released is False
        assert outcome.open_debt_count >= 1
        lease = db_session.get(DeviceLease, lease.id)
        assert is_debt_lease(lease)
        assert lease.status == LeaseStatus.ACTIVE.value
        assert lease.job_id is None
        assert (
            db_session.query(DeviceReturnDebt)
            .filter_by(device_id=device.id, state="open", family="power-cycle")
            .count()
        ) == 1
        db_session.refresh(device)
        assert device.lease_generation >= 2
        assert lease.fencing_token == f"{device.id}:{device.lease_generation}"

    def test_replay_does_not_create_second_open_debt(self, db_session):
        device, job, lease, _ = _seed(db_session)
        handle_job_lease_release_sync(
            db_session, device_id=device.id, job_id=job.id, job=job,
        )
        db_session.commit()
        handle_job_lease_release_sync(
            db_session, device_id=device.id, job_id=job.id, job=job,
        )
        db_session.commit()
        assert (
            db_session.query(DeviceReturnDebt)
            .filter_by(device_id=device.id, state="open", family="power-cycle")
            .count()
        ) == 1

    def test_partial_discharge_keeps_debt_lease(self, db_session, monkeypatch):
        device, job, lease, _ = _seed(
            db_session,
            disarm_results={
                "power-cycle": {"result": "not_executed"},
                "sleep": {"result": "incomplete"},
            },
        )
        outcome = handle_job_lease_release_sync(
            db_session, device_id=device.id, job_id=job.id, job=job,
        )
        db_session.commit()
        assert outcome.open_debt_count == 2
        lease = db_session.get(DeviceLease, lease.id)
        trusted = trust_test_probe(db_session, monkeypatch, device.id)
        result = apply_discharge_sync(
            db_session,
            device_id=device.id,
            fencing_token=lease.fencing_token,
            family_results={"power-cycle": trusted},
        )
        db_session.commit()
        assert result["ok"] is True
        assert result["debt_lease_released"] is False
        assert result["open_debt_count"] == 1
        lease = db_session.get(DeviceLease, lease.id)
        assert is_debt_lease(lease)

    def test_override_marks_unverified_and_releases_when_last(self, db_session):
        device, job, lease, _ = _seed(db_session)
        handle_job_lease_release_sync(
            db_session, device_id=device.id, job_id=job.id, job=job,
        )
        db_session.commit()
        result = apply_override_sync(
            db_session,
            device_id=device.id,
            families=["power-cycle"],
            reason_category="flashed",
            evidence_ref="job:999",
        )
        db_session.commit()
        assert result["ok"] is True
        assert result["closed_count"] == 1
        assert result["debt_lease_released"] is True
        db_session.refresh(device)
        assert device.return_state["power-cycle"]["state"] == "unverified"
        assert device.return_state["power-cycle"]["source"] == "override"
        lease = db_session.get(DeviceLease, lease.id)
        assert lease.status == LeaseStatus.RELEASED.value

    def test_retire_and_unretire_with_debt(self, db_session):
        device, job, lease, _ = _seed(db_session)
        handle_job_lease_release_sync(
            db_session, device_id=device.id, job_id=job.id, job=job,
        )
        db_session.commit()
        device = retire_device(
            db_session,
            device_id=device.id,
            reason="u3-test",
            actor_id=None,
            actor_username="admin",
        )
        assert device.retired_at is not None
        lease = db_session.get(DeviceLease, lease.id)
        assert lease.status == LeaseStatus.RELEASED.value
        assert (
            db_session.query(DeviceReturnDebt)
            .filter_by(device_id=device.id, state="open")
            .count()
        ) == 1
        device = unretire_device(
            db_session,
            device_id=device.id,
            reason="u3-restore",
            actor_id=None,
            actor_username="admin",
        )
        assert device.retired_at is None
        active = (
            db_session.query(DeviceLease)
            .filter_by(device_id=device.id, status=LeaseStatus.ACTIVE.value)
            .one()
        )
        assert is_debt_lease(active)

    def test_emergency_release_rejects_debt_lease(self, db_session):
        device, job, lease, _ = _seed(db_session)
        handle_job_lease_release_sync(
            db_session, device_id=device.id, job_id=job.id, job=job,
        )
        db_session.commit()
        lease = db_session.get(DeviceLease, lease.id)
        with pytest.raises(Conflict) as ei:
            release_terminal_job_lease(
                db_session,
                device_id=device.id,
                lease_id=lease.id,
                reason="force",
                user_id=1,
                username="admin",
            )
        assert ei.value.detail["code"] == "LEASE_IS_RETURN_DEBT"

    def test_admission_reason_device_return_debt(self, db_session):
        device, job, lease, _ = _seed(db_session)
        handle_job_lease_release_sync(
            db_session, device_id=device.id, job_id=job.id, job=job,
        )
        db_session.commit()
        unavailable, _ = _classify_dispatch_devices_sync(db_session, [device.id])
        assert any(u["reason"] == "device_return_debt" for u in unavailable)

    def test_executed_unverified_releases_without_debt(self, db_session):
        device, job, lease, _ = _seed(
            db_session,
            disarm_results={"power-cycle": {"result": "executed_unverified"}},
        )
        outcome = handle_job_lease_release_sync(
            db_session, device_id=device.id, job_id=job.id, job=job,
        )
        db_session.commit()
        assert outcome.released is True
        assert outcome.converted_to_debt is False
        assert (
            db_session.query(DeviceReturnDebt)
            .filter_by(device_id=device.id, state="open")
            .count()
        ) == 0
        lease = db_session.get(DeviceLease, lease.id)
        assert lease.status == LeaseStatus.RELEASED.value

    def test_init_idle_device_idempotent(self, db_session):
        device, job, lease, _ = _seed(
            db_session,
            disarm_results={"power-cycle": {"result": "executed_unverified"}},
        )
        # Free the JOB lease first so init sees idle device
        handle_job_lease_release_sync(
            db_session, device_id=device.id, job_id=job.id, job=job,
        )
        db_session.commit()
        s1 = init_device_return_sync(db_session, device_id=device.id, dry_run=False)
        db_session.commit()
        s2 = init_device_return_sync(db_session, device_id=device.id, dry_run=False)
        db_session.commit()
        assert s1["status"] == "ok"
        assert s2["status"] == "ok"
        db_session.refresh(device)
        assert device.return_state is not None
        assert device.return_state["power-cycle"]["state"] == "unverified"
