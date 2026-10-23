"""I5 real API: a third PG session uses NOWAIT to inspect each blocked prefix."""
import threading
import time

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from backend.core.database import SessionLocal, get_db
from backend.main import fastapi_app
from backend.models.device_lease import DeviceLease
from backend.models.host import Device
from backend.models.job import JobInstance
from backend.tests.api.test_disarm_policy_u8_3752 import (
    combination, result_entry, seed, signals_are_forbidden,  # noqa: F401
)


@pytest.mark.parametrize("blocked", ["job", "device", "lease"])
def test_init_api_i5_with_third_session_nowait(client, seed, combination, monkeypatch, blocked):
    entered = threading.Event()
    finished = threading.Event()
    worker_pids, responses, errors = [], [], []

    def session_per_request():
        with SessionLocal() as db:
            db.execute(text("SET LOCAL lock_timeout = '3s'"))
            worker_pids.append(db.execute(text("SELECT pg_backend_pid()")).scalar_one())
            entered.set()
            yield db
    monkeypatch.setitem(fastapi_app.dependency_overrides, get_db, session_per_request)

    def request():
        try:
            responses.append(client.post(f'/api/v1/agent/jobs/{seed["job_id"]}/return-check', json={
                "fencing_token": "u8-test-token", "family_results": {"power-cycle": result_entry(combination)}}))
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()

    # A blocks one stage. B runs the actual HTTP route with its own Session.
    # C probes NOWAIT; no assertion depends on a wall-clock-only sleep.
    holder = SessionLocal()
    if blocked == "job":
        holder.execute(select(JobInstance).where(JobInstance.id == seed["job_id"]).with_for_update()).one()
    elif blocked == "device":
        holder.execute(select(Device).where(Device.id == seed["device_id"]).with_for_update()).one()
    else:
        holder.execute(select(DeviceLease).where(DeviceLease.device_id == seed["device_id"]).with_for_update()).one()
    worker = threading.Thread(target=request, daemon=True)
    worker.start()
    try:
        assert entered.wait(2), errors
        with SessionLocal() as reader:
            for _ in range(400):
                waiting = reader.execute(text(
                    "SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"), {"pid": worker_pids[0]}).scalar()
                if waiting == "Lock":
                    break
                assert not finished.is_set(), (errors, responses)
                time.sleep(0.005)  # bounded condition polling for actual PG lock wait
            else:
                raise AssertionError("init request never reached the held row")

        with SessionLocal() as probe:
            def lockable(model, predicate):
                try:
                    probe.execute(select(model).where(predicate).with_for_update(nowait=True)).one()
                    return True
                except OperationalError as exc:
                    assert exc.orig.sqlstate == "55P03"
                    return False
                finally:
                    probe.rollback()
            if blocked == "job":
                assert lockable(Device, Device.id == seed["device_id"])
                assert lockable(DeviceLease, DeviceLease.device_id == seed["device_id"])
            elif blocked == "device":
                assert not lockable(JobInstance, JobInstance.id == seed["job_id"])
                assert lockable(DeviceLease, DeviceLease.device_id == seed["device_id"])
            else:
                assert not lockable(JobInstance, JobInstance.id == seed["job_id"])
                assert not lockable(Device, Device.id == seed["device_id"])
    finally:
        holder.rollback()
        holder.close()
        assert finished.wait(5), "request did not finish after releasing holder"
        worker.join(1)
    assert not errors, errors
    assert responses[0].status_code == 200, responses[0].text
    with SessionLocal() as db:
        assert db.get(Device, seed["device_id"]).return_state["power-cycle"]["state"] == "returned"
