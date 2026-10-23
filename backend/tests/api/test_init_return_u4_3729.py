"""#3723 v1.7.15–17: real init-check API and terminal separation, isolated PG."""
from datetime import datetime, timezone
from copy import deepcopy

import pytest

from backend.core.database import SessionLocal
from backend.models.audit import AuditLog
from backend.models.device_lease import DeviceLease
from backend.models.device_return_debt import DeviceReturnDebt
from backend.models.host import Device
from backend.models.job import JobInstance, StepTrace
from backend.tests.api.test_disarm_policy_u8_3752 import (
    combination, result_entry, seed, signals_are_forbidden,  # noqa: F401
)


def report(client, seed, results, token="u8-test-token"):
    return client.post(f'/api/v1/agent/jobs/{seed["job_id"]}/return-check', json={
        "fencing_token": token, "family_results": results,
    })


@pytest.mark.parametrize("value,state", [("executed_unverified", "unverified"),
    ("failed", "debt"), ("incomplete", "debt"), ("not_executed", "debt")])
def test_untrusted_nonprivileged_init_result_persists_and_replays_once(client, seed, combination, value, state):
    item = {**result_entry(combination, "wrong_apk"), "result": value}
    response = report(client, seed, {"power-cycle": item})
    assert response.status_code == 200, response.text
    assert report(client, seed, {"power-cycle": item}).status_code == 200
    with SessionLocal() as db:
        device = db.get(Device, seed["device_id"])
        assert device.return_state["power-cycle"]["state"] == state
        assert device.return_state["power-cycle"]["source"] == "init_check"
        rows = db.query(DeviceReturnDebt).filter_by(device_id=device.id).all()
        assert len(rows) == (1 if state == "debt" else 0)
        if rows:
            assert rows[0].source_kind == "job" and rows[0].source_job_id == seed["job_id"]
            assert rows[0].cause == "init_check:" + value
        assert db.get(JobInstance, seed["job_id"]).disarm_results is None


@pytest.mark.parametrize("value", ["confirmed", "readback_failed"])
def test_untrusted_privileged_init_rejects_without_business_write(client, seed, combination, value):
    with SessionLocal() as db:
        before = db.get(Device, seed["device_id"]).return_state
    response = report(client, seed, {"power-cycle": {**result_entry(combination, "wrong_apk"), "result": value}})
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "UNVERIFIED_PROBE_RESULT"
    with SessionLocal() as db:
        assert db.get(Device, seed["device_id"]).return_state == before
        assert db.query(DeviceReturnDebt).count() == 0
        assert db.query(AuditLog).filter_by(action="unverified_probe_result").count() == 1


def test_init_token_rejection_precedes_policy(client, seed, combination):
    response = report(client, seed, {"power-cycle": result_entry(combination, "wrong_apk")}, token="invalid-test-token")
    assert response.status_code == 409, response.text
    with SessionLocal() as db:
        assert db.query(AuditLog).filter_by(action="unverified_probe_result").count() == 0
        assert db.query(DeviceReturnDebt).count() == 0


def test_init_confirmed_closes_all_family_debts_and_keeps_job_lease(client, seed, combination):
    with SessionLocal() as db:
        for source in ("stock", "job"):
            db.add(DeviceReturnDebt(device_id=seed["device_id"], family="power-cycle", source_kind=source,
                source_job_id=seed["job_id"] if source == "job" else None, state="open", cause="incomplete"))
        db.commit()
    response = report(client, seed, {"power-cycle": result_entry(combination)})
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        rows = db.query(DeviceReturnDebt).filter_by(device_id=seed["device_id"]).all()
        assert len(rows) == 2 and all(r.state == "closed" and r.closed_reason == "discharged" for r in rows)
        lease = db.query(DeviceLease).filter_by(device_id=seed["device_id"], status="ACTIVE").one()
        assert lease.job_id == seed["job_id"] and lease.lease_type == "JOB"
        assert db.get(Device, seed["device_id"]).return_state["power-cycle"]["state"] == "returned"


def test_partial_init_rejection_commits_legal_family_and_downgrade_is_accepted(client, seed, combination):
    item = result_entry(combination, "wrong_apk")
    response = report(client, seed, {"power-cycle": {**item, "result": "executed_unverified"}, "sleep": item})
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["accepted_families"] == ["power-cycle"]
    with SessionLocal() as db:
        states = db.get(Device, seed["device_id"]).return_state
        assert states["power-cycle"]["state"] == "unverified" and "sleep" not in states
    assert report(client, seed, {"power-cycle": item}).status_code == 422
    assert report(client, seed, {"power-cycle": {**item, "result": "executed_unverified"}}).status_code == 200


@pytest.mark.parametrize("value,state", [("confirmed", "returned"), ("executed_unverified", "unverified")])
def test_non_powercycle_job_completion_preserves_init_state(client, seed, combination, value, state):
    """v1.7.17 replaces the superseded mixed terminal-result preflight."""
    with SessionLocal() as db:
        db.get(JobInstance, seed["job_id"]).pipeline_def = {"lifecycle": {"init": [], "teardown": []}}
        db.query(StepTrace).filter_by(job_id=seed["job_id"]).delete()
        db.add(StepTrace(job_id=seed["job_id"], step_id="return_check.power-cycle", stage="init",
                         status="COMPLETED", event_type="COMPLETED", original_ts=datetime.now(timezone.utc), exit_code=0))
        db.commit()
    item = {**result_entry(combination), "result": value, "step_id": "return_check.power-cycle"}
    assert report(client, seed, {"power-cycle": item}).status_code == 200
    with SessionLocal() as db:
        before = db.get(Device, seed["device_id"]).return_state
    # Init results travel only through the new endpoint, never /complete.
    response = client.post(f'/api/v1/agent/jobs/{seed["job_id"]}/complete', json={
        "update": {"status": "FINISHED"}, "fencing_token": "u8-test-token"})
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        assert db.get(Device, seed["device_id"]).return_state == before
        assert before["power-cycle"]["state"] == state
        assert db.query(DeviceReturnDebt).filter_by(device_id=seed["device_id"], state="open").count() == 0


def test_powercycle_init_and_own_teardown_are_independent(client, seed, combination):
    assert report(client, seed, {"power-cycle": result_entry(combination)}).status_code == 200
    response = client.post(f'/api/v1/agent/jobs/{seed["job_id"]}/complete', json={
        "update": {"status": "FINISHED"}, "fencing_token": "u8-test-token",
        "disarm_results": {"power-cycle": {"result": "failed", "step_id": "disarm", "exit_code": 1}}})
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        job = db.get(JobInstance, seed["job_id"])
        assert job.disarm_results["power-cycle"]["step_id"] == "disarm"
        assert job.disarm_results["power-cycle"]["result"] == "failed"
        assert db.get(Device, seed["device_id"]).return_state["power-cycle"]["state"] == "debt"


def test_heartbeat_declares_init_endpoint(client, seed):
    response = client.post("/api/v1/heartbeat", json={"host_id": seed["host_id"],
        "status": "ONLINE", "mount_status": {}})
    assert response.status_code == 200, response.text
    assert response.json()["return_init_check_available"] is True


def test_unverified_init_does_not_close_existing_debt(client, seed, combination):
    with SessionLocal() as db:
        db.add(DeviceReturnDebt(device_id=seed["device_id"], family="power-cycle", source_kind="stock",
                                state="open", cause="incomplete"))
        db.commit()
    item = {**result_entry(combination, "wrong_apk"), "result": "executed_unverified"}
    assert report(client, seed, {"power-cycle": item}).status_code == 200
    with SessionLocal() as db:
        assert db.query(DeviceReturnDebt).filter_by(device_id=seed["device_id"], state="open").count() == 1
        assert db.get(Device, seed["device_id"]).return_state["power-cycle"]["state"] == "debt"


def test_closed_init_failure_replay_does_not_reopen_the_same_source(client, seed, combination):
    failed = {**result_entry(combination, "wrong_apk"), "result": "failed"}
    assert report(client, seed, {"power-cycle": failed}).status_code == 200
    assert report(client, seed, {"power-cycle": result_entry(combination)}).status_code == 200
    assert report(client, seed, {"power-cycle": failed}).status_code == 200
    with SessionLocal() as db:
        rows = db.query(DeviceReturnDebt).filter_by(device_id=seed["device_id"]).all()
        assert len(rows) == 1 and rows[0].state == "closed"
        assert db.get(Device, seed["device_id"]).return_state["power-cycle"]["state"] == "returned"


def test_distinct_init_lease_reports_have_distinct_diagnostic_identity(client, seed, combination, db_session):
    item = result_entry(combination, "wrong_apk")
    assert report(client, seed, {"power-cycle": item}).status_code == 422
    with SessionLocal() as db:
        lease = db.query(DeviceLease).filter_by(device_id=seed["device_id"], status="ACTIVE").one()
        lease.fencing_token = "next-init-test-token"
        db.commit()
    db_session.expire_all()
    assert report(client, seed, {"power-cycle": item}, token="next-init-test-token").status_code == 422
    assert report(client, seed, {"power-cycle": item}, token="next-init-test-token").status_code == 422
    with SessionLocal() as db:
        assert db.query(AuditLog).filter_by(action="unverified_probe_result").count() == 2


@pytest.mark.parametrize("trusted,own_footprint,expected", [(True, False, "returned"),
    (False, False, "unverified"), (True, True, "debt")])
def test_frozen_engine_init_api_and_complete_keep_result_channels_separate(
    client, seed, monkeypatch, tmp_path, trusted, own_footprint, expected,
):
    """Frozen script + real Engine/runner + real init/complete APIs + isolated PG."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import requests
    from backend.agent import pipeline_runner
    from backend.agent.api_client import _build_complete_payload
    from backend.agent.pipeline_engine import StepResult
    from backend.agent.tests.return_frozen_3729 import FrozenReturnDevice
    from backend.services import agent_completion
    from backend.core import task_queue

    device_io = FrozenReturnDevice(monkeypatch, tmp_path, trusted=trusted)
    monkeypatch.setattr(agent_completion.PlanAggregator, "on_job_terminal", AsyncMock())
    monkeypatch.setattr(agent_completion, "broadcast_run_job_update", AsyncMock())
    monkeypatch.setattr(task_queue, "get_queue", lambda: SimpleNamespace(enqueue=AsyncMock()))
    monkeypatch.setattr(pipeline_runner, "get_run_log_dir", lambda _: str(tmp_path / "runner-logs"))
    pipeline = {"lifecycle": {"init": [], "teardown": []}}
    if own_footprint:
        pipeline["lifecycle"] = {
            "init": [{"step_id": "own_setup", "action": "script:powercycle_setup"}],
            "teardown": [{"step_id": "own_disarm", "action": "script:powercycle_disarm",
                           "version": "1.0.8", "timeout_seconds": 660}],
        }
    with SessionLocal() as db:
        db.get(JobInstance, seed["job_id"]).pipeline_def = pipeline
        device = db.get(Device, seed["device_id"])
        device.platform, device.model = "MTK", device_io.model
        db.query(StepTrace).filter_by(job_id=seed["job_id"]).delete()
        db.commit()

    def real_http(url, *, json, **kwargs):
        if url.endswith("/return-check"):
            device_io.reports.append(deepcopy(json))
        path = url.removeprefix("http://u4-test.invalid")
        received = client.post(path, json=json)
        response = requests.Response()
        response.status_code, response._content, response.url = received.status_code, received.content, url
        return response
    monkeypatch.setattr(requests, "post", real_http)

    def started(engine):
        original = engine._resolve_action

        def resolve(action, step):
            if action == "script:powercycle_setup":
                def setup(ctx):
                    assert device_io.reports[0]["family_results"]["power-cycle"]["result"] == "confirmed"
                    device_io.kind = "denied"
                    return StepResult(success=True)
                return setup
            return original(action, step)
        engine._resolve_action = resolve
    try:
        payload = pipeline_runner.execute_pipeline_run(pipeline, seed["job_id"], "u4-test-device",
            device_io.engine()._adb, "http://u4-test.invalid", seed["host_id"],
            script_registry=device_io.registry, local_db=device_io.local_db, mq_producer=device_io.mq,
            fencing_token="u8-test-token", operation_scheduler=device_io.scheduler,
            device_id=seed["device_id"], on_engine_started=started)
        assert payload["status"] == "FINISHED", payload
        with SessionLocal() as db:
            initial_state = db.get(Device, seed["device_id"]).return_state
        assert initial_state["power-cycle"]["source"] == "init_check"
        if own_footprint:
            entry = payload["disarm_results"]["power-cycle"]
            assert entry["step_id"] == "own_disarm" and entry["result"] == "failed"
        else:
            assert not payload.get("disarm_results")
        # Persist exactly the real Engine's trace output as evidence for completion.
        with SessionLocal() as db:
            for call in device_io.mq.send_step_trace.call_args_list:
                trace = call.kwargs
                db.add(StepTrace(job_id=seed["job_id"], step_id=trace["step_id"], stage=trace["stage"],
                    event_type=trace["event_type"], status=trace["status"], output=trace.get("output"),
                    exit_code=trace.get("exit_code"), original_ts=datetime.now(timezone.utc)))
            db.commit()
        response = client.post(f'/api/v1/agent/jobs/{seed["job_id"]}/complete',
                               json=_build_complete_payload(payload, "u8-test-token"))
        assert response.status_code == 200, response.text
        with SessionLocal() as db:
            state = db.get(Device, seed["device_id"]).return_state
            assert state["power-cycle"]["state"] == expected
            if not own_footprint:
                assert state == initial_state
            assert db.query(DeviceReturnDebt).filter_by(device_id=seed["device_id"], state="open").count() == int(own_footprint)
        assert all(not e.get("step_id", "").startswith("return_check.") for e in payload.get("disarm_results", {}).values())
    finally:
        device_io.close()
        # A failed own teardown converts the JOB lease, clearing its job_id.
        # The inherited seed cleanup only knows the original JOB association.
        with SessionLocal() as db:
            db.query(DeviceReturnDebt).filter_by(device_id=seed["device_id"]).delete()
            db.query(DeviceLease).filter_by(device_id=seed["device_id"]).delete()
            db.commit()
