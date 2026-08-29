"""#519 — unified risk aggregation (DLE authority + unlinked signals)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from backend.models.device_log_event import DeviceLogEvent
from backend.models.job import JobInstance, JobLogSignal
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services.log_observation import aggregate_risk_summary


def _seed_job(db_session, sample_device):
    now = datetime.now(timezone.utc)
    plan = Plan(name="risk-obs-plan", failure_threshold=0.05)
    db_session.add(plan)
    db_session.flush()
    pr = PlanRun(
        plan_id=plan.id,
        status="RUNNING",
        failure_threshold=0.05,
        plan_snapshot={},
        run_type="MANUAL",
    )
    db_session.add(pr)
    db_session.flush()
    job = JobInstance(
        plan_run_id=pr.id,
        plan_id=plan.id,
        device_id=sample_device.id,
        host_id=sample_device.host_id,
        status="RUNNING",
        pipeline_def={"lifecycle": {}},
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(job)
    db_session.flush()
    return job, now


def test_risk_summary_counts_unlinked_signals_only(db_session, sample_device):
    job, now = _seed_job(db_session, sample_device)
    for i in range(3):
        db_session.add(JobLogSignal(
            job_id=job.id,
            host_id=str(sample_device.host_id),
            device_serial=sample_device.serial,
            seq_no=i,
            category="ANR",
            source="inotifyd",
            path_on_device=f"/data/anr/{i}",
            detected_at=now,
            received_at=now,
            extra={"event_subtype": "ANR", "nfs_path": f"/nfs/anr/{i}"},
        ))
    db_session.commit()

    summary = aggregate_risk_summary(db_session, [job.id])
    assert summary is not None
    assert summary["counts"]["by_type"]["ANR"] == 3


def test_risk_summary_prefers_dle_and_skips_linked_signals(db_session, sample_device):
    job, now = _seed_job(db_session, sample_device)
    dle = DeviceLogEvent(
        id=uuid4(),
        serial=sample_device.serial,
        platform="MTK",
        event_type="ANR",
        event_subtype="ANR",
        detected_at=now,
        state="REMOTE",
        local_path="/local/anr/1",
        remote_path="/nfs/devices/1/anr/1",
        host_id=str(sample_device.host_id),
        job_id=job.id,
        plan_run_id=job.plan_run_id,
    )
    db_session.add(dle)
    db_session.flush()

    db_session.add(JobLogSignal(
        job_id=job.id,
        host_id=str(sample_device.host_id),
        device_serial=sample_device.serial,
        device_log_event_id=dle.id,
        seq_no=1,
        category="ANR",
        source="reconciler",
        path_on_device="/data/anr/1",
        detected_at=now,
        received_at=now,
        extra={"event_subtype": "ANR", "nfs_path": "/nfs/devices/1/anr/1"},
    ))
    db_session.add(JobLogSignal(
        job_id=job.id,
        host_id=str(sample_device.host_id),
        device_serial=sample_device.serial,
        seq_no=2,
        category="ANR",
        source="inotifyd",
        path_on_device="/data/anr/legacy",
        detected_at=now,
        received_at=now,
        extra={"event_subtype": "ANR", "nfs_path": "/nfs/anr/legacy"},
    ))
    db_session.commit()

    summary = aggregate_risk_summary(db_session, [job.id])
    assert summary is not None
    # 1 from DLE + 1 legacy unlinked signal (linked signal excluded)
    assert summary["counts"]["by_type"]["ANR"] == 2
