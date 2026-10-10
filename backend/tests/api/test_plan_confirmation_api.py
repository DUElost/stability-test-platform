"""确认指纹的 preview / run HTTP 契约（#3653 §3.2，#3655）。"""

from __future__ import annotations

from backend.models.job import JobInstance
from backend.models.plan import PlanStep
from backend.models.plan_run import PlanRun, PlanRunTargetDevice
from backend.models.resource_pool import ResourceAllocation
from backend.services.plan_confirmation import (
    confirmation_fingerprint,
    load_plan_graph_for_confirmation,
)
from backend.services.plan_dispatcher_sync import _fetch_script_metadata

PREFIX = "stp-l1-v1:"
MESSAGE = "计划配置已变化，请重新预览并确认"


def _counts(db) -> tuple[int, int, int, int]:
    return (
        db.query(PlanRun).count(),
        db.query(PlanRunTargetDevice).count(),
        db.query(JobInstance).count(),
        db.query(ResourceAllocation).count(),
    )


def _expected(db, plan_id: int) -> str:
    plan, steps = load_plan_graph_for_confirmation(db, plan_id)
    meta = _fetch_script_metadata(db, steps)
    return confirmation_fingerprint(plan, list(steps), meta)


def test_preview_fingerprint_matches_the_loaded_graph(
    client, auth_headers, db_session, sample_plan, sample_script, sample_device,
):
    expected = _expected(db_session, sample_plan.id)
    first = client.post(
        f"/api/v1/plans/{sample_plan.id}/run/preview",
        json={"device_ids": [sample_device.id]},
        headers=auth_headers,
    )
    second = client.post(
        f"/api/v1/plans/{sample_plan.id}/run/preview",
        json={"device_ids": [sample_device.id, sample_device.id + 50]},
        headers=auth_headers,
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    token = first.json()["data"]["confirmation_fingerprint"]
    assert token == expected
    assert token == second.json()["data"]["confirmation_fingerprint"]
    assert token.startswith(PREFIX)


def test_run_without_token_and_with_null_stays_compatible(
    client, auth_headers, db_session, sample_plan, sample_script, sample_device,
):
    omitted = client.post(
        f"/api/v1/plans/{sample_plan.id}/run",
        json={"device_ids": [sample_device.id]},
        headers=auth_headers,
    )
    assert omitted.status_code == 200, omitted.text
    assert omitted.json()["data"]["status"] == "QUEUED"
    explicit_null = client.post(
        f"/api/v1/plans/{sample_plan.id}/run",
        json={"device_ids": [sample_device.id], "confirmation_fingerprint": None},
        headers=auth_headers,
    )
    assert explicit_null.status_code == 200, explicit_null.text
    assert explicit_null.json()["data"]["status"] == "QUEUED"


def test_run_with_the_preview_token_queues(
    client, auth_headers, db_session, sample_plan, sample_script, sample_device,
):
    preview = client.post(
        f"/api/v1/plans/{sample_plan.id}/run/preview",
        json={"device_ids": [sample_device.id]},
        headers=auth_headers,
    )
    assert preview.status_code == 200, preview.text
    token = preview.json()["data"]["confirmation_fingerprint"]
    run = client.post(
        f"/api/v1/plans/{sample_plan.id}/run",
        json={"device_ids": [sample_device.id], "confirmation_fingerprint": token},
        headers=auth_headers,
    )
    assert run.status_code == 200, run.text
    assert run.json()["data"]["status"] == "QUEUED"


def test_wrong_token_is_409_and_creates_nothing(
    client, auth_headers, db_session, sample_plan, sample_script, sample_device,
):
    step = db_session.query(PlanStep).filter_by(plan_id=sample_plan.id).one()
    step.params = {"password": "SENTINEL_PASSWORD"}
    db_session.commit()
    preview = client.post(
        f"/api/v1/plans/{sample_plan.id}/run/preview",
        json={"device_ids": [sample_device.id]},
        headers=auth_headers,
    )
    token = preview.json()["data"]["confirmation_fingerprint"]
    sample_plan.name = "changed-after-preview"
    step.timeout_seconds = 99
    db_session.commit()
    before = _counts(db_session)
    wrong = token[:-1] + ("0" if token[-1] != "0" else "1")
    resp = client.post(
        f"/api/v1/plans/{sample_plan.id}/run",
        json={"device_ids": [sample_device.id], "confirmation_fingerprint": wrong},
        headers=auth_headers,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == {
        "code": "PLAN_CONFIRMATION_CHANGED",
        "message": MESSAGE,
    }
    assert "stp-l1-v1" not in resp.text
    assert "SENTINEL_PASSWORD" not in resp.text
    assert _counts(db_session) == before


def test_illegal_token_is_422_and_creates_nothing(
    client, auth_headers, db_session, sample_plan, sample_script, sample_device,
):
    illegal = [
        "",
        "nope",
        PREFIX + "AB" * 32,
        PREFIX + "a" * 63,
    ]
    for value in illegal:
        before = _counts(db_session)
        resp = client.post(
            f"/api/v1/plans/{sample_plan.id}/run",
            json={"device_ids": [sample_device.id], "confirmation_fingerprint": value},
            headers=auth_headers,
        )
        assert resp.status_code == 422, resp.text
        preview = client.post(
            f"/api/v1/plans/{sample_plan.id}/run/preview",
            json={"device_ids": [sample_device.id], "confirmation_fingerprint": value},
            headers=auth_headers,
        )
        assert preview.status_code == 422, preview.text
        assert _counts(db_session) == before


def test_run_requires_auth(client, sample_plan, sample_device):
    resp = client.post(
        f"/api/v1/plans/{sample_plan.id}/run",
        json={"device_ids": [sample_device.id]},
    )
    assert resp.status_code == 401
