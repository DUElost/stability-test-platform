"""PENDING 120s timeout + SocketIO job_status integration (T-B7)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from backend.core.job_timeout_config import DISPATCHED_TIMEOUT_SECONDS
from backend.models.enums import JobStatus
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.scheduler import recycler

pytestmark = pytest.mark.integration

PIPELINE_DEF = {"lifecycle": {"init": [], "teardown": []}}


def test_pending_timeout_emits_job_status_and_releases_lease(
    db_session, gate_chain,
):
    now = datetime.now(timezone.utc)
    old_created = now - timedelta(seconds=DISPATCHED_TIMEOUT_SECONDS + 30)

    pr = PlanRun(
        plan_id=gate_chain["plan"].id,
        status="RUNNING",
        failure_threshold=0.1,
        plan_snapshot={"plan_id": gate_chain["plan"].id},
        run_type="MANUAL",
        triggered_by="integration-test",
        started_at=old_created,
    )
    db_session.add(pr)
    db_session.flush()

    job = JobInstance(
        plan_run_id=pr.id,
        plan_id=gate_chain["plan"].id,
        device_id=gate_chain["device_a"].id,
        host_id=gate_chain["host_a"].id,
        status=JobStatus.PENDING.value,
        pipeline_def=PIPELINE_DEF,
        created_at=old_created,
        updated_at=old_created,
    )
    db_session.add(job)
    db_session.commit()

    emitted: list[tuple] = []

    def _capture_emit(event, payload, *, namespace, room):
        emitted.append((event, payload, namespace, room))

    with patch("backend.scheduler.recycler.schedule_emit", side_effect=_capture_emit), patch(
        "backend.scheduler.recycler._fill_deferred_post_completions", lambda db, current: 0,
    ), patch(
        "backend.scheduler.recycler._prune_steptrace_artifacts", lambda db, current: None,
    ):
        recycler.recycle_once()

    db_session.expire_all()
    refreshed = db_session.get(JobInstance, job.id)
    assert refreshed is not None
    assert refreshed.status == JobStatus.FAILED.value
    assert "pending_timeout" in (refreshed.status_reason or "")

    job_status_events = [e for e in emitted if e[0] == "job_status"]
    assert len(job_status_events) >= 1
    _event, payload, namespace, room = job_status_events[0]
    assert namespace == "/dashboard"
    assert room == f"plan_run:{pr.id}"
    assert payload["payload"]["job_id"] == job.id
    assert payload["payload"]["status"] == "FAILED"
