"""#529 — PlanRun log-events endpoint (DLE archive authority)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from backend.models.device_log_event import DeviceLogEvent
from backend.models.job import JobInstance, JobLogSignal
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun


def _seed_plan_run(db_session, sample_device):
    now = datetime.now(timezone.utc)
    plan = Plan(name="log-events-plan", failure_threshold=0.05)
    db_session.add(plan)
    db_session.flush()
    pr = PlanRun(
        plan_id=plan.id,
        status="SUCCESS",
        failure_threshold=0.05,
        plan_snapshot={},
        run_type="MANUAL",
        started_at=now,
        ended_at=now,
    )
    db_session.add(pr)
    db_session.flush()
    job = JobInstance(
        plan_run_id=pr.id,
        plan_id=plan.id,
        device_id=sample_device.id,
        host_id=sample_device.host_id,
        status="COMPLETED",
        pipeline_def={"lifecycle": {}},
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(job)
    db_session.flush()
    return pr, job, now


def test_plan_run_log_events_lists_dle_rows(client, auth_headers, db_session, sample_device):
    pr, job, now = _seed_plan_run(db_session, sample_device)
    event_id = uuid4()
    db_session.add(DeviceLogEvent(
        id=event_id,
        serial=sample_device.serial,
        platform="MTK",
        event_type="AEE",
        event_subtype="KE",
        detected_at=now,
        state="REMOTE",
        local_path="/local/aee/1",
        remote_path="/nfs/devices/1/aee/1",
        host_id=str(sample_device.host_id),
        job_id=job.id,
        plan_run_id=pr.id,
    ))
    db_session.commit()

    resp = client.get(
        f"/api/v1/plan-runs/{pr.id}/log-events",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["plan_run_id"] == pr.id
    assert data["data_authority"] == "device_log_event"
    assert data["total"] == 1
    assert data["items"][0]["id"] == str(event_id)
    assert data["items"][0]["remote_path"] == "/nfs/devices/1/aee/1"


def test_watcher_summary_read_repair_links_signal_and_reports_stats(
    client, auth_headers, db_session, sample_device,
):
    pr, job, now = _seed_plan_run(db_session, sample_device)
    event_id = uuid4()
    db_session.add(DeviceLogEvent(
        id=event_id,
        serial=sample_device.serial,
        platform="MTK",
        event_type="AEE",
        event_subtype="KE",
        detected_at=now,
        state="LOCAL",
        local_path="/local/aee/1",
        host_id=str(sample_device.host_id),
        job_id=job.id,
        plan_run_id=pr.id,
        signal_seq_no=1,
    ))
    db_session.add(JobLogSignal(
        job_id=job.id,
        host_id=str(sample_device.host_id),
        device_serial=sample_device.serial,
        seq_no=1,
        category="AEE",
        source="reconciler",
        path_on_device="/data/aee/1",
        detected_at=now,
        received_at=now,
    ))
    db_session.commit()

    resp = client.get(
        f"/api/v1/plan-runs/{pr.id}/watcher-summary?window_minutes=60",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    link_stats = resp.json()["data"]["archive"]["link_stats"]
    assert link_stats["linked_signals"] == 1
    assert link_stats["link_rate"] == 1.0
