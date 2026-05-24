"""Tests for POST /plan-runs/{id}/retry-dispatch."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.orm.attributes import flag_modified

from backend.models.plan_run import PlanRun


@pytest.fixture
def failed_precheck_run(db_session, sample_plan):
    run_ctx = {
        "dispatch_device_ids": [1],
        "dispatch_state": {
            "enqueue_key": "precheck:99",
            "requeue_attempts": 0,
            "status": "failed",
            "enqueued_at": "2026-05-10T07:00:00.000Z",
            "started_at": "2026-05-10T07:00:05.000Z",
            "completed_at": "2026-05-10T07:01:00.000Z",
            "last_error": "precheck:sync_failed: host-1:rpc_failed",
        },
        "precheck": {
            "phase": "failed",
            "started_at": "2026-05-10T07:00:05.000Z",
            "completed_at": "2026-05-10T07:01:00.000Z",
            "hosts": {},
            "errors": ["sync_failed: host-1"],
        },
    }
    pr = PlanRun(
        plan_id=sample_plan.id,
        status="FAILED",
        failure_threshold=sample_plan.failure_threshold,
        plan_snapshot={"plan_id": sample_plan.id, "steps": []},
        run_type="MANUAL",
        run_context=run_ctx,
        result_summary={"precheck_failed": True, "reason": "sync_failed"},
        triggered_by="test",
    )
    db_session.add(pr)
    db_session.commit()
    db_session.refresh(pr)
    return pr


def test_retry_dispatch_re_enqueues_gate(
    client, auth_headers, db_session, failed_precheck_run, sample_device,
):
    """Failed precheck PlanRun can be reset and re-enqueued."""
    run_id = failed_precheck_run.id
    failed_precheck_run.run_context["dispatch_device_ids"] = [sample_device.id]
    flag_modified(failed_precheck_run, "run_context")
    db_session.commit()

    enqueue_calls = []

    with patch(
        "backend.services.plan_precheck.enqueue_sync",
        side_effect=lambda *args, **kwargs: enqueue_calls.append(kwargs),
    ), patch(
        "backend.services.plan_precheck.initialise_precheck_state",
        return_value={"phase": "verifying", "hosts": {}, "errors": []},
    ):
        resp = client.post(
            f"/api/v1/plan-runs/{run_id}/retry-dispatch",
            headers=auth_headers,
        )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["plan_run_id"] == run_id
    assert body["status"] == "RUNNING"
    assert len(enqueue_calls) == 1
    assert enqueue_calls[0]["key"] == f"precheck:{run_id}"
    assert enqueue_calls[0]["retries"] == 1

    db_session.refresh(failed_precheck_run)
    assert failed_precheck_run.status == "RUNNING"
    assert failed_precheck_run.result_summary is None


def test_retry_dispatch_requires_auth(client, failed_precheck_run):
    resp = client.post(f"/api/v1/plan-runs/{failed_precheck_run.id}/retry-dispatch")
    assert resp.status_code == 401
