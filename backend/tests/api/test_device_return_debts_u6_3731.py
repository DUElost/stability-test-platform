"""#3723 U6：GET /devices/{id}/return-debts（隔离 PG）。

Contract under test (v1.7.9 §3.7 U6.1):
- same auth as device detail (logged-in user);
- only open rows, oldest first;
- 404 for an unknown device, 401 for anonymous;
- never returns evidence.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.models.device_return_debt import DeviceReturnDebt
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun

_NOW = datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc)
_EXPECTED_KEYS = {
    "id",
    "family",
    "source_kind",
    "source_job_id",
    "cause",
    "created_at",
}


def _debt(device_id: int, **overrides) -> DeviceReturnDebt:
    payload = {
        "device_id": device_id,
        "family": "power-cycle",
        "source_kind": "job",
        "source_job_id": None,
        "cause": "not_executed",
        "state": "open",
        "created_at": _NOW,
        "evidence": {"raw": "must-not-leak"},
    }
    payload.update(overrides)
    return DeviceReturnDebt(**payload)


def test_anonymous_is_rejected(client, sample_device):
    resp = client.get(f"/api/v1/devices/{sample_device.id}/return-debts")
    assert resp.status_code == 401


def test_unknown_device_returns_404(client, auth_headers):
    resp = client.get("/api/v1/devices/999999/return-debts", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "DEVICE_NOT_FOUND"


def test_only_open_rows_in_creation_order_without_evidence(
    client, db_session, sample_device, auth_headers
):
    plan = Plan(name="u6-return-debts", description="u6", created_by="test")
    db_session.add(plan)
    db_session.flush()
    run = PlanRun(
        plan_id=plan.id,
        status="RUNNING",
        plan_snapshot={"name": plan.name},
        run_type="MANUAL",
        triggered_by="test",
    )
    db_session.add(run)
    db_session.flush()
    job = JobInstance(
        plan_run_id=run.id,
        plan_id=plan.id,
        device_id=sample_device.id,
        host_id=sample_device.host_id,
        status="FAILED",
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
    )
    db_session.add(job)
    db_session.flush()

    older = _debt(
        sample_device.id,
        family="sleep",
        source_kind="job",
        source_job_id=job.id,
        cause="incomplete",
        created_at=_NOW - timedelta(hours=2),
    )
    newer = _debt(
        sample_device.id,
        family="power-cycle",
        source_kind="stock",
        source_job_id=None,
        cause="failed",
        created_at=_NOW - timedelta(hours=1),
    )
    closed = _debt(
        sample_device.id,
        family="gpu",
        cause="readback_failed",
        state="closed",
        closed_reason="discharged",
        closed_at=_NOW,
        created_at=_NOW - timedelta(hours=3),
    )
    db_session.add_all([newer, older, closed])
    db_session.commit()

    resp = client.get(
        f"/api/v1/devices/{sample_device.id}/return-debts", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    rows = body["data"]
    assert [row["family"] for row in rows] == ["sleep", "power-cycle"]
    assert [row["id"] for row in rows] == [older.id, newer.id]
    assert all(set(row) == _EXPECTED_KEYS for row in rows)
    assert "evidence" not in rows[0]
    assert rows[0]["source_job_id"] == job.id
    assert rows[1]["source_kind"] == "stock"
    assert rows[1]["source_job_id"] is None
    assert rows[0]["cause"] == "incomplete"


def test_empty_list_is_a_normal_empty_state(client, sample_device, auth_headers):
    resp = client.get(
        f"/api/v1/devices/{sample_device.id}/return-debts", headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == []
