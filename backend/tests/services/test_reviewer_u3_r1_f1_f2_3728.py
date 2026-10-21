"""#3728 / PR #3748 review-r1 F1/F2 regressions (autoflush=False + lock refresh).

Authoritative probe from review comment 6253538954; assertions must not be weakened.
"""
from __future__ import annotations

import os
from backend.tests.disarm_probe_evidence import trust_test_probe
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.database import AsyncSessionLocal, SessionLocal, get_db
from backend.main import fastapi_app
from backend.models.device_lease import DeviceLease
from backend.models.enums import JobStatus
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun
from backend.models.device_return_debt import DeviceReturnDebt
from backend.models.host import Device
from backend.scripts import init_device_return as cli
from backend.services import device_return as dr
from backend.tests.services.test_device_return_u3_3728 import _seed
from backend.tests.services.test_init_device_return_cli_3728 import _seed_plain_device

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
IS_PG = TEST_DATABASE_URL.startswith("postgresql")
pytestmark = pytest.mark.skipif(
    not IS_PG, reason="F1/F2 require PostgreSQL SessionLocal + row locks"
)


def _create_debt(db_session, results=None):
    device, job, lease, _ = _seed(db_session, disarm_results=results)
    ids = (device.id, job.id, lease.id)
    with SessionLocal() as db:
        out = dr.handle_job_lease_release_sync(db, device_id=ids[0], job_id=ids[1])
        db.commit()
        assert out.converted_to_debt
        token = db.get(DeviceLease, ids[2]).fencing_token
    return (*ids, token)


def _runtime_sync_dependency():
    with SessionLocal() as db:
        assert db.autoflush is False
        yield db


@pytest.mark.parametrize("entry", ["override", "discharge"])
def test_real_sync_routes_close_last_debt_with_runtime_session(
    client, db_session, admin_headers, monkeypatch, entry
):
    device_id, job_id, lease_id, token = _create_debt(db_session)
    trusted = trust_test_probe(db_session, monkeypatch, device_id)
    monkeypatch.setitem(fastapi_app.dependency_overrides, get_db, _runtime_sync_dependency)
    if entry == "override":
        response = client.post(
            f"/api/v1/devices/{device_id}/return-override",
            json={
                "families": ["power-cycle"],
                "reason_category": "flashed",
                "evidence_ref": "review-only",
            },
            headers=admin_headers,
        )
    else:
        response = client.post(
            f"/api/v1/agent/devices/{device_id}/return-discharge",
            json={
                "fencing_token": token,
                "family_results": {"power-cycle": trusted},
            },
        )
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        device = db.get(Device, device_id)
        lease = db.get(DeviceLease, lease_id)
        opened = db.query(DeviceReturnDebt).filter_by(device_id=device_id, state="open").count()
        facts = {
            "response": response.json(),
            "open_rows": opened,
            "lease_status": lease.status,
            "return_state": device.return_state,
        }
        assert opened == 0, facts
        assert lease.status == "RELEASED", facts
        assert device.return_state["power-cycle"]["state"] == ("unverified" if entry == "override" else "returned"), facts
        assert response.json()["data"]["open_debt_count"] == 0, facts


def test_serialized_overrides_reread_device_json_after_preload(db_session, engine):
    device_id, job_id, lease_id, _ = _create_debt(
        db_session,
        {"power-cycle": {"result": "not_executed"}, "sleep": {"result": "incomplete"}},
    )
    with Session(bind=engine, autoflush=True, expire_on_commit=False) as left:
        held = left.get(Device, device_id)
        assert held.return_state["power-cycle"]["state"] == "debt"
        with Session(bind=engine, autoflush=True, expire_on_commit=False) as right:
            out = dr.apply_override_sync(
                right,
                device_id=device_id,
                families=["power-cycle"],
                reason_category="flashed",
                evidence_ref="first",
            )
            right.commit()
            assert out["open_debt_count"] == 1
        dr.apply_override_sync(
            left,
            device_id=device_id,
            families=["sleep"],
            reason_category="flashed",
            evidence_ref="second",
        )
        left.commit()
    with SessionLocal() as db:
        device = db.get(Device, device_id)
        assert db.query(DeviceReturnDebt).filter_by(device_id=device_id, state="open").count() == 0
        assert db.get(DeviceLease, lease_id).status == "RELEASED"
        assert device.return_state["power-cycle"]["state"] == "unverified", device.return_state
        assert device.return_state["sleep"]["state"] == "unverified", device.return_state


@pytest.mark.parametrize("apply", [False, True])
def test_cli_actual_keyset_reader_and_cross_device_nowait(db_session, monkeypatch, apply):
    first = _seed_plain_device(db_session)
    second = _seed_plain_device(db_session)
    ids = [first.id, second.id]
    monkeypatch.setattr(cli, "PAGE_SIZE", 1)
    seen = []

    def entering(device_id):
        seen.append(device_id)
        if device_id == ids[1]:
            with SessionLocal() as probe:
                probe.execute(
                    select(Device).where(Device.id == ids[0]).with_for_update(nowait=True)
                )
                probe.rollback()

    monkeypatch.setattr(cli, "_device_enter_hook", entering)
    result = cli.main(["--apply" if apply else "--dry-run", "--limit", "2"])
    assert result == 0
    assert seen == ids
    with SessionLocal() as db:
        assert db.query(DeviceReturnDebt).count() == 0
        assert db.query(DeviceLease).count() == 0
        for did in ids:
            state = db.get(Device, did).return_state
            assert bool(state) is apply


def test_unified_entry_keeps_caller_pending_device_change(db_session):
    """#3723 §3.4.3-1: populate_existing must not drop caller unflushed Device edits."""
    device, job, lease, _ = _seed(db_session, disarm_results={"power-cycle": {"result": "confirmed"}})
    device_id, job_id = device.id, job.id
    with SessionLocal() as db:
        assert db.autoflush is False
        db.get(Device, device_id).status = "OFFLINE"  # 调用方未 flush 的改动
        dr.handle_job_lease_release_sync(db, device_id=device_id, job_id=job_id)
        db.commit()
    with SessionLocal() as db:
        assert db.get(Device, device_id).status == "OFFLINE"


@pytest.mark.asyncio(loop_scope="module")
async def test_async_entry_rereads_device_after_preload(db_session):
    """#3723 §3.4.3-2: async lock must refresh preloaded Device after peer override."""
    device, job, lease, _ = _seed(
        db_session,
        disarm_results={
            "power-cycle": {"result": "not_executed"},
            "sleep": {"result": "incomplete"},
        },
    )
    device_id = device.id
    plan_id = job.plan_id
    host_id = job.host_id
    with SessionLocal() as db:
        dr.handle_job_lease_release_sync(db, device_id=device_id, job_id=job.id)
        db.commit()
    run2 = PlanRun(
        plan_id=plan_id,
        status="RUNNING",
        plan_snapshot={"name": "r2"},
        run_type="MANUAL",
        triggered_by="test",
    )
    db_session.add(run2)
    db_session.commit()
    async with AsyncSessionLocal() as adb:
        held = await adb.get(Device, device_id)  # 预读：power-cycle 为 debt
        assert held.return_state["power-cycle"]["state"] == "debt"
        with SessionLocal() as other:  # 另一事务放行 power-cycle
            dr.apply_override_sync(
                other,
                device_id=device_id,
                families=["power-cycle"],
                reason_category="flashed",
                evidence_ref="r2-async",
            )
            other.commit()
        job2 = JobInstance(
            plan_run_id=run2.id,
            plan_id=plan_id,
            device_id=device_id,
            host_id=host_id,
            status=JobStatus.COMPLETED.value,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
            ended_at=datetime.now(timezone.utc),
            disarm_results={"sleep": {"result": "incomplete"}},
        )
        adb.add(job2)
        await adb.flush()
        await dr.handle_job_lease_release(adb, device_id=device_id, job_id=job2.id, job=job2)
        await adb.commit()
    with SessionLocal() as db:
        assert db.get(Device, device_id).return_state["power-cycle"]["state"] == "unverified"
