"""ADR-0065 / #4206 U4 — PostgreSQL interleaving C4–C8 (finishing_guard service).

Pattern: ``backend/tests/services/test_device_return_lock_order_3728.py``.
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from backend.agent.contracts.capabilities import (
    ATOMIC_COMMIT,
    FINISHING_GUARD_V2,
    FORCE_ABORT,
)
from backend.core.database import SessionLocal
from backend.models.device_return_debt import DeviceReturnDebt
from backend.models.device_lease import DeviceLease
from backend.models.enums import JobStatus, LeaseStatus, LeaseType
from backend.models.finishing_guard_admission import FinishingGuardAdmission
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services import device_return as dr
from backend.services.finishing_guard import (
    ActivatePermitIn,
    FinishingGuardError,
    PreRegisterIn,
    activate_permit_sync,
    close_admission_sync,
    pre_register_atomic_commit_sync,
)
from backend.services.plan_run_abort import abort_plan_run

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
IS_PG = TEST_DATABASE_URL.startswith("postgresql")
pytestmark = pytest.mark.skipif(not IS_PG, reason="U4 interleaving requires PostgreSQL")

_NOW = datetime.now(timezone.utc)


def _set_timeouts(db):
    db.execute(text("SET LOCAL deadlock_timeout = '200ms'"))
    db.execute(text("SET LOCAL lock_timeout = '8s'"))


def _ensure_admission_row(db) -> None:
    if db.get(FinishingGuardAdmission, 1) is None:
        db.add(FinishingGuardAdmission(id=1, admission_open=True))
        db.flush()


def _seed_running_atomic_job(db) -> tuple[JobInstance, PlanRun, Host, Device]:
    _ensure_admission_row(db)
    suffix = uuid4().hex[:8]
    host = Host(
        id=f"fg-{suffix}",
        hostname=f"fg-{suffix}",
        status="ONLINE",
        last_heartbeat=_NOW,
        created_at=_NOW,
        boot_id=f"boot-{suffix}",
        agent_capabilities=[
            FINISHING_GUARD_V2,
            ATOMIC_COMMIT,
            FORCE_ABORT,
        ],
    )
    device = Device(
        serial=f"FG-{suffix}",
        host_id=host.id,
        status="ONLINE",
        tags=[],
        created_at=_NOW,
        last_seen=_NOW,
        lease_generation=1,
    )
    plan = Plan(name=f"fg-{suffix}", description="u4", created_by="test")
    db.add_all([host, device, plan])
    db.flush()
    run = PlanRun(
        plan_id=plan.id,
        status="RUNNING",
        plan_snapshot={"name": plan.name, "plan_id": plan.id},
        run_type="MANUAL",
        triggered_by="test",
        started_at=_NOW,
        total_job_count=1,
    )
    db.add(run)
    db.flush()
    job = JobInstance(
        plan_run_id=run.id,
        plan_id=plan.id,
        device_id=device.id,
        host_id=host.id,
        status=JobStatus.RUNNING.value,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        negotiated_capabilities={
            FINISHING_GUARD_V2: True,
            ATOMIC_COMMIT: True,
            FORCE_ABORT: True,
        },
        started_at=_NOW,
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
        agent_instance_id=host.id,
        acquired_at=_NOW,
        renewed_at=_NOW,
        expires_at=_NOW + timedelta(hours=1),
    )
    db.add(lease)
    db.commit()
    for row in (host, device, job, run, lease):
        db.refresh(row)
    return job, run, host, device


class TestC4PreRegisterAbort:
    def test_abort_before_pre_register_rejects(self, db_session):
        job, run, _host, _dev = _seed_running_atomic_job(db_session)

        def do_abort():
            with SessionLocal() as db:
                _set_timeouts(db)
                abort_plan_run(
                    run.id, db=db, reason="race", force=False,
                )
                db.commit()
            return True

        def do_pre_reg():
            with SessionLocal() as db:
                _set_timeouts(db)
                try:
                    pre_register_atomic_commit_sync(
                        db,
                        job_id=job.id,
                        payload=PreRegisterIn(
                            request_id="req-a",
                            min_critical_section_seconds=1,
                        ),
                    )
                    db.commit()
                    return "ok"
                except FinishingGuardError as exc:
                    db.rollback()
                    return exc.code
            return "unknown"

        with ThreadPoolExecutor(max_workers=2) as pool:
            f_abort = pool.submit(do_abort)
            f_pre = pool.submit(do_pre_reg)
            results = {f_abort.result(timeout=30), f_pre.result(timeout=30)}
        assert True in results
        assert "ok" in results or "abort_already_committed" in results


class TestC6CloseAdmission:
    def test_close_admission_blocks_late_pre_register(self, db_session):
        job, _run, _host, _dev = _seed_running_atomic_job(db_session)
        with SessionLocal() as db:
            _set_timeouts(db)
            close_admission_sync(db)
            db.commit()
        with SessionLocal() as db:
            _set_timeouts(db)
            with pytest.raises(FinishingGuardError) as exc:
                pre_register_atomic_commit_sync(
                    db,
                    job_id=job.id,
                    payload=PreRegisterIn(
                        request_id="req-b",
                        min_critical_section_seconds=1,
                    ),
                )
            assert exc.value.code == "admission_closed"


class TestC8FlashIntegrityDebt:
    def test_closing_flash_only_keeps_b6_debt(self, db_session):
        job, _run, _host, device = _seed_running_atomic_job(db_session)
        now = _NOW
        dr._upsert_open_debt_sync(
            db_session,
            device_id=device.id,
            family="power-cycle",
            source_kind="job",
            source_job_id=job.id,
            source_fencing_token="t0",
            cause="test",
            evidence={},
            now=now,
        )
        dr._upsert_open_debt_sync(
            db_session,
            device_id=device.id,
            family="flash_integrity",
            source_kind="job",
            source_job_id=job.id,
            source_fencing_token="t0",
            cause="atomic_pre_register",
            evidence={},
            now=now,
        )
        db_session.commit()
        with SessionLocal() as db:
            dr.close_flash_integrity_debts_sync(db, device_id=device.id, now=now)
            db.commit()
        open_rows = db_session.execute(
            select(DeviceReturnDebt.family).where(
                DeviceReturnDebt.device_id == device.id,
                DeviceReturnDebt.state == "open",
            )
        ).scalars().all()
        assert open_rows == ["power-cycle"]


class TestC5ActivatePermit:
    def test_activate_permit_grants(self, db_session):
        job, _run, host, _dev = _seed_running_atomic_job(db_session)
        with SessionLocal() as db:
            _set_timeouts(db)
            pre_register_atomic_commit_sync(
                db,
                job_id=job.id,
                payload=PreRegisterIn(
                    request_id="req-c",
                    min_critical_section_seconds=1,
                ),
            )
            db.commit()
        with SessionLocal() as db:
            _set_timeouts(db)
            out = activate_permit_sync(
                db,
                job_id=job.id,
                host_id=host.id,
                payload=ActivatePermitIn(
                    request_id="req-c",
                    client_boot_id=host.boot_id or "",
                ),
            )
            db.commit()
        assert out["status"] == "granted"