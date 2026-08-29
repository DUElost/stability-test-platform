"""#556 — signal↔DLE link repair moved off the watcher-summary read path.

Both cases assert through a **fresh session**, because the original defect was
invisible to the request's own session: the UPDATE was never committed, so a
same-transaction read still saw the repaired rows.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from backend.core.database import SessionLocal
from backend.models.device_log_event import DeviceLogEvent
from backend.models.job import JobInstance, JobLogSignal
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.scheduler.signal_link_reconciler import reconcile_signal_links_once


def _seed_unlinked_signal(db_session, sample_device):
    """PlanRun + job + a DLE whose signal landed without a link."""
    now = datetime.now(timezone.utc)
    plan = Plan(name="signal-link-plan", failure_threshold=0.05)
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
        signal_seq_no=7,
    ))
    db_session.add(JobLogSignal(
        job_id=job.id,
        host_id=str(sample_device.host_id),
        device_serial=sample_device.serial,
        seq_no=7,
        category="AEE",
        source="reconciler",
        path_on_device="/sdcard/aee/1",
        detected_at=now,
        received_at=now,
        extra={"event_subtype": "KE", "nfs_path": "/nfs/devices/1/aee/1"},
    ))
    db_session.commit()
    return pr, job, event_id


def _link_state(job_id: int):
    """Read device_log_event_id through a brand-new session."""
    with SessionLocal() as db:
        signal = (
            db.query(JobLogSignal)
            .filter(JobLogSignal.job_id == job_id)
            .one()
        )
        return signal.device_log_event_id


def test_reconcile_links_backlog_signal(db_session, sample_device):
    """The sweep persists the link — visible to a different session."""
    _pr, job, event_id = _seed_unlinked_signal(db_session, sample_device)
    assert _link_state(job.id) is None

    summary = reconcile_signal_links_once()

    assert summary["scanned"] == 1, summary
    assert summary["linked"] == 1, summary
    assert _link_state(job.id) == event_id


def test_reconcile_is_idempotent(db_session, sample_device):
    """Second tick finds nothing left to do."""
    _pr, job, _event_id = _seed_unlinked_signal(db_session, sample_device)
    reconcile_signal_links_once()

    summary = reconcile_signal_links_once()

    assert summary["scanned"] == 0, summary
    assert summary["linked"] == 0, summary


def test_watcher_summary_no_longer_links_signals(
    client, auth_headers, db_session, sample_device,
):
    """#556 regression: the GET must not write, and must report true state."""
    pr, job, _event_id = _seed_unlinked_signal(db_session, sample_device)

    resp = client.get(
        f"/api/v1/plan-runs/{pr.id}/watcher-summary",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Still unlinked in a fresh session → the request wrote nothing.
    assert _link_state(job.id) is None

    # link_stats reflects the database, not an uncommitted repair.
    stats = resp.json()["data"]["archive"]["link_stats"]
    assert stats["total_signals"] == 1, stats
    assert stats["linked_signals"] == 0, stats
    assert stats["unlinked_linkable"] == 1, stats
