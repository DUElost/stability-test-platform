"""POST/GET /agent/device-log-events — DeviceLogEvent ingest (ADR-0028 D1)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("DATABASE_URL", "").startswith("sqlite"),
    reason="需要 PostgreSQL（与 agent_api_watcher 同约定）",
)

from backend.api.routes.agent_api import (
    DeviceLogEventBatchIn,
    DeviceLogEventIn,
    ingest_device_log_events,
    list_device_log_events,
)
from backend.core.database import AsyncSessionLocal, SessionLocal
from backend.models.device_log_event import DeviceLogEvent
from backend.models.enums import EventState, HostStatus, JobStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance, JobLogSignal
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun


def _seed_host_job() -> dict:
    suffix = uuid4().hex[:8]
    host_id = f"host-dle-{suffix}"
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        host = Host(id=host_id, hostname=f"dle-{suffix}", status=HostStatus.ONLINE.value, created_at=now)
        device = Device(
            serial=f"serial-{suffix}",
            host_id=host_id,
            status="ONLINE",
            tags=[],
            created_at=now,
            adb_connected=True,
            adb_state="device",
        )
        plan = Plan(
            name=f"plan-dle-{suffix}",
            description="device-log-event-test",
            failure_threshold=0.05,
            created_by="pytest",
        )
        db.add_all([host, device, plan])
        db.flush()
        db.add(PlanStep(
            plan_id=plan.id,
            step_key="check",
            script_name="check_device",
            script_version="1.0.0",
            stage="init",
            sort_order=0,
        ))
        run = PlanRun(
            plan_id=plan.id,
            status="RUNNING",
            failure_threshold=0.05,
            plan_snapshot={"plan": {"id": plan.id, "name": plan.name}, "steps": []},
            run_type="MANUAL",
            triggered_by="pytest",
        )
        db.add(run)
        db.flush()
        job = JobInstance(
            plan_run_id=run.id,
            plan_id=plan.id,
            device_id=device.id,
            host_id=host_id,
            status=JobStatus.RUNNING.value,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
            created_at=now,
            updated_at=now,
            started_at=now,
        )
        db.add(job)
        db.commit()
        return {
            "host_id": host_id,
            "device_id": device.id,
            "job_id": job.id,
            "plan_id": plan.id,
            "plan_run_id": run.id,
            "serial": device.serial,
        }
    finally:
        db.close()


def _cleanup(seed: dict) -> None:
    db = SessionLocal()
    try:
        db.query(JobLogSignal).filter(JobLogSignal.job_id == seed["job_id"]).delete()
        db.query(DeviceLogEvent).filter(DeviceLogEvent.host_id == seed["host_id"]).delete()
        db.query(JobInstance).filter(JobInstance.id == seed["job_id"]).delete()
        db.query(PlanRun).filter(PlanRun.id == seed["plan_run_id"]).delete()
        db.query(PlanStep).filter(PlanStep.plan_id == seed["plan_id"]).delete()
        db.query(Plan).filter(Plan.id == seed["plan_id"]).delete()
        db.query(Device).filter(Device.id == seed["device_id"]).delete()
        db.query(Host).filter(Host.id == seed["host_id"]).delete()
        db.commit()
    finally:
        db.close()


@pytest.mark.asyncio(loop_scope="module")
async def test_device_log_events_create_and_update(monkeypatch, tmp_path):
    nfs = tmp_path / "nfs"
    nfs.mkdir()
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(nfs))
    seed = _seed_host_job()
    try:
        db = SessionLocal()
        try:
            sig = JobLogSignal(
                job_id=seed["job_id"],
                host_id=seed["host_id"],
                device_serial=seed["serial"],
                seq_no=7,
                category="AEE",
                source="reconciler",
                path_on_device="/data/aee_exp/db_history",
                detected_at=datetime.now(timezone.utc),
                received_at=datetime.now(timezone.utc),
            )
            db.add(sig)
            db.commit()
        finally:
            db.close()

        create_ev = DeviceLogEventIn(
            serial=seed["serial"],
            platform="MTK",
            event_type="KE",
            detected_at=datetime.now(timezone.utc).isoformat(),
            state=EventState.LOCAL.value,
            local_path="/mnt/hdd/aee_events/dev/ke_001",
            host_id=seed["host_id"],
            job_id=seed["job_id"],
            plan_run_id=seed["plan_run_id"],
            link_signal_seq_no=7,
        )
        async with AsyncSessionLocal() as db:
            r1 = await ingest_device_log_events(
                DeviceLogEventBatchIn(events=[create_ev]),
                db=db,
                _=None,
            )
        assert r1.data["upserted"] == 1
        assert len(r1.data.get("event_ids", [])) == 1

        db = SessionLocal()
        try:
            rows = db.query(DeviceLogEvent).filter(DeviceLogEvent.host_id == seed["host_id"]).all()
            assert len(rows) == 1
            event_id = rows[0].id
            assert rows[0].state == EventState.LOCAL.value
            assert rows[0].signal_seq_no == 7

            sig = db.query(JobLogSignal).filter(
                JobLogSignal.job_id == seed["job_id"],
                JobLogSignal.seq_no == 7,
            ).one()
            assert sig.device_log_event_id == event_id
        finally:
            db.close()

        remote_path = str(nfs / "devices" / str(seed["plan_run_id"]) / "ke_001")
        update_ev = DeviceLogEventIn(
            id=str(event_id),
            serial=seed["serial"],
            platform="MTK",
            event_type="KE",
            detected_at=datetime.now(timezone.utc).isoformat(),
            state=EventState.REMOTE.value,
            local_path="/mnt/hdd/aee_events/dev/ke_001",
            remote_path=remote_path,
            checksum="abc123",
            host_id=seed["host_id"],
            plan_run_id=seed["plan_run_id"],
        )
        async with AsyncSessionLocal() as db:
            r2 = await ingest_device_log_events(
                DeviceLogEventBatchIn(events=[update_ev]),
                db=db,
                _=None,
            )
        assert r2.data["upserted"] == 1

        async with AsyncSessionLocal() as db:
            listed = await list_device_log_events(
                host_id=seed["host_id"],
                state=EventState.REMOTE.value,
                db=db,
                _=None,
            )
        assert listed.data["total"] == 1
        assert listed.data["events"][0]["state"] == EventState.REMOTE.value
    finally:
        _cleanup(seed)


@pytest.mark.asyncio(loop_scope="module")
async def test_device_log_event_links_when_signal_arrives_later():
    """#214: DLE ingest before job_log_signal; reverse-link on late signal."""
    from backend.services.device_log_event import link_signals_to_device_log_events

    seed = _seed_host_job()
    try:
        create_ev = DeviceLogEventIn(
            serial=seed["serial"],
            platform="MTK",
            event_type="JE",
            detected_at=datetime.now(timezone.utc).isoformat(),
            state=EventState.LOCAL.value,
            local_path="/mnt/hdd/aee_events/dev/je_001",
            host_id=seed["host_id"],
            job_id=seed["job_id"],
            plan_run_id=seed["plan_run_id"],
            link_signal_seq_no=3,
        )
        async with AsyncSessionLocal() as db:
            r1 = await ingest_device_log_events(
                DeviceLogEventBatchIn(events=[create_ev]),
                db=db,
                _=None,
            )
        assert r1.data["upserted"] == 1
        event_id = r1.data["event_ids"][0]

        db = SessionLocal()
        try:
            sig = JobLogSignal(
                job_id=seed["job_id"],
                host_id=seed["host_id"],
                device_serial=seed["serial"],
                seq_no=3,
                category="AEE",
                source="reconciler",
                path_on_device="/data/aee_exp/db_history",
                detected_at=datetime.now(timezone.utc),
                received_at=datetime.now(timezone.utc),
            )
            db.add(sig)
            db.commit()
            assert sig.device_log_event_id is None
        finally:
            db.close()

        async with AsyncSessionLocal() as db:
            await link_signals_to_device_log_events(db, [seed["job_id"]])
            await db.commit()

        db = SessionLocal()
        try:
            sig = db.query(JobLogSignal).filter(
                JobLogSignal.job_id == seed["job_id"],
                JobLogSignal.seq_no == 3,
            ).one()
            assert str(sig.device_log_event_id) == event_id
        finally:
            db.close()
    finally:
        _cleanup(seed)
