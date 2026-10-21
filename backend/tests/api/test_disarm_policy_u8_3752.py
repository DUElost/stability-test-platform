"""D2.5 /complete and discharge real API matrix (test-only combinations)."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.agent.contracts.device_return import ONBOARDED
from backend.core.database import SessionLocal
from backend.models.audit import AuditLog
from backend.models.device_lease import DeviceLease
from backend.models.host import Device
from backend.models.job import JobInstance, StepTrace
from backend.models.device_return_debt import DeviceReturnDebt
from backend.services import agent_completion
from backend.services.device_return import manifest_package_sha, DEBT_LEASE_REASON, DEBT_LEASE_HOLDER
from backend.tests.scheduler.test_disarm_phase_u8_3752 import seed, signals_are_forbidden  # noqa: F401


@pytest.fixture
def combination(seed, monkeypatch, db_session):
    combination = {
        "platform": "TEST", "model": "TEST_MODEL", "permission_model": "root",
        "autotesttool_apk_sha256": "a"*64, "script_name": "powercycle_disarm", "script_version": "1.0.8",
        "package_sha256": manifest_package_sha("powercycle_disarm", "1.0.8"), "report_ref": "test-only://report",
    }
    monkeypatch.setitem(ONBOARDED, "power-cycle", {**ONBOARDED["power-cycle"], "verified_device_classes": [combination]})
    monkeypatch.setattr(agent_completion.PlanAggregator, "on_job_terminal", AsyncMock())
    monkeypatch.setattr(agent_completion, "broadcast_run_job_update", AsyncMock())
    from backend.core import task_queue
    monkeypatch.setattr(task_queue, "get_queue", lambda: SimpleNamespace(enqueue=AsyncMock()))
    with SessionLocal() as db:
        device = db.get(Device, seed["device_id"])
        device.platform, device.model = "TEST", "TEST_MODEL"
        device.return_state = {"power-cycle": {"state": "unverified", "source": "stock"}}
        for sid, stage in [("setup", "init"), ("disarm", "teardown")]:
            db.add(StepTrace(job_id=seed["job_id"], step_id=sid, stage=stage, event_type="COMPLETED",
                             status="COMPLETED", exit_code=0, original_ts=datetime.now(timezone.utc)))
        db.commit()
    yield combination
    # The test client deliberately keeps the injected Session open. A rejected
    # sync request can retain its read locks until this explicit rollback.
    db_session.rollback()
    with SessionLocal() as db:
        db.query(DeviceReturnDebt).filter_by(device_id=seed["device_id"]).delete()
        db.query(DeviceLease).filter_by(device_id=seed["device_id"]).delete()
        db.commit()


def result_entry(combination, changed=None):
    evidence = {**combination, "mode": "disarm", "readback": "clear", "kind": "ok", "success": True, "boot_completed": "1"}
    if changed == "unverified":
        evidence["permission_model"] = "non-root"
    elif changed == "unknown":
        evidence["permission_model"] = "unknown"
    elif changed == "wrong_apk":
        evidence["autotesttool_apk_sha256"] = "c"*64
    elif changed == "wrong_package":
        evidence["package_sha256"] = "c"*64
    elif changed == "forged_verified":
        evidence["autotesttool_apk_sha256"] = ""
        evidence["verified_category"] = True
    return {"result": "confirmed", "step_id": "disarm", "exit_code": 0, "evidence": evidence}


@pytest.mark.parametrize("changed", [None, "unverified", "unknown", "wrong_apk", "wrong_package", "forged_verified"])
def test_complete_keeps_test_conclusion_and_audits_untrusted_result(client, seed, combination, changed):
    payload = {"update": {"status": "FINISHED"}, "fencing_token": "u8-test-token",
               "disarm_results": {"power-cycle": result_entry(combination, changed)}}
    response = client.post(f'/api/v1/agent/jobs/{seed["job_id"]}/complete', json=payload)
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        job = db.get(JobInstance, seed["job_id"])
        assert job.status == "COMPLETED"
        assert job.disarm_results["power-cycle"]["result"] == ("confirmed" if changed is None else "executed_unverified")
        audit = db.query(AuditLog).filter_by(action="unverified_probe_result", resource_id=str(seed["job_id"])).all()
        assert len(audit) == (0 if changed is None else 1)
        if changed is not None:
            assert audit[0].details["raw"]["result"] == "confirmed"
    # Accepted terminal replay must remain 200; no outbox poison 422.
    assert client.post(f'/api/v1/agent/jobs/{seed["job_id"]}/complete', json=payload).status_code == 200


@pytest.mark.parametrize("changed", [None, "unverified", "unknown", "wrong_apk", "wrong_package", "forged_verified"])
def test_discharge_rejects_only_untrusted_speciality_before_business_write(client, seed, combination, changed):
    with SessionLocal() as db:
        lease = db.query(DeviceLease).filter_by(device_id=seed["device_id"]).one()
        lease.lease_type, lease.job_id = "MAINTENANCE", None
        lease.reason, lease.holder = DEBT_LEASE_REASON, DEBT_LEASE_HOLDER
        device = db.get(Device, seed["device_id"])
        before = device.return_state
        db.commit()
    response = client.post(f'/api/v1/agent/devices/{seed["device_id"]}/return-discharge', json={
        "fencing_token": "u8-test-token", "family_results": {"power-cycle": result_entry(combination, changed)}})
    assert response.status_code == (200 if changed is None else 422), response.text
    with SessionLocal() as db:
        device = db.get(Device, seed["device_id"])
        if changed is not None:
            assert device.return_state == before
            assert response.json()["detail"]["code"] == "UNVERIFIED_PROBE_RESULT"
            assert db.query(AuditLog).filter_by(action="unverified_probe_result", resource_id=str(seed["device_id"])).count() == 1


@pytest.mark.parametrize("kind,expected", [("wall_clock", "incomplete"), ("stall", "incomplete"), ("script_fail", "failed")])
def test_engine_runner_complete_storage_and_non_onboarded_debt(client, seed, combination, monkeypatch, tmp_path, kind, expected):
    from backend.agent.tests.test_disarm_u8_3752 import interrupted_engine_payload
    from backend.agent.api_client import _build_complete_payload

    pipeline, result = interrupted_engine_payload(monkeypatch, tmp_path, kind)
    assert result["disarm_results"]["sleep"]["result"] == expected
    with SessionLocal() as db:
        db.get(JobInstance, seed["job_id"]).pipeline_def = pipeline
        db.commit()
    payload = _build_complete_payload(result, "u8-test-token")
    response = client.post(f'/api/v1/agent/jobs/{seed["job_id"]}/complete', json=payload)
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        assert db.get(JobInstance, seed["job_id"]).status == "COMPLETED"
        assert db.get(JobInstance, seed["job_id"]).disarm_results["sleep"]["result"] == expected
        debts = db.query(DeviceReturnDebt).filter_by(device_id=seed["device_id"], family="sleep", state="open").all()
        assert bool(debts) == (expected == "incomplete")
        assert db.get(Device, seed["device_id"]).return_state["sleep"]["state"] == ("debt" if expected == "incomplete" else "unverified")


@pytest.mark.parametrize("changed", [None, "unverified", "unknown", "wrong_apk", "wrong_package", "forged_verified"])
def test_late_confirmation_only_closes_trusted_same_job_source(client, seed, combination, changed):
    original = {"update": {"status": "FINISHED"}, "fencing_token": "u8-test-token",
                "disarm_results": {"power-cycle": {"result": "incomplete", "step_id": "disarm"}}}
    first = client.post(f'/api/v1/agent/jobs/{seed["job_id"]}/complete', json=original)
    assert first.status_code == 200, first.text
    with SessionLocal() as db:
        db.add(DeviceReturnDebt(device_id=seed["device_id"], family="power-cycle", source_kind="stock",
                               source_job_id=None, cause="incomplete", state="open"))
        db.commit()
    late = {**original, "disarm_results": {"power-cycle": result_entry(combination, changed)}}
    response = client.post(f'/api/v1/agent/jobs/{seed["job_id"]}/complete', json=late)
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        rows = db.query(DeviceReturnDebt).filter_by(device_id=seed["device_id"]).all()
        assert next(r for r in rows if r.source_kind=="stock").state == "open"
        assert next(r for r in rows if r.source_kind=="job").state == ("closed" if changed is None else "open")
        assert db.get(JobInstance, seed["job_id"]).status == "COMPLETED"
        assert db.query(DeviceLease).filter_by(device_id=seed["device_id"], status="ACTIVE").count() == 1


def test_discharge_partial_acceptance_commits_trusted_family_and_keeps_rejected_debt(client, seed, combination):
    with SessionLocal() as db:
        lease = db.query(DeviceLease).filter_by(device_id=seed["device_id"]).one()
        lease.lease_type, lease.job_id = "MAINTENANCE", None
        lease.reason, lease.holder = DEBT_LEASE_REASON, DEBT_LEASE_HOLDER
        for family in ("power-cycle", "sleep"):
            db.add(DeviceReturnDebt(device_id=seed["device_id"], family=family, source_kind="stock",
                                   cause="incomplete", state="open"))
        db.commit()
    response = client.post(f'/api/v1/agent/devices/{seed["device_id"]}/return-discharge', json={
        "fencing_token": "u8-test-token", "family_results": {
            "power-cycle": result_entry(combination), "sleep": result_entry(combination)}})
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["discharged_families"] == ["power-cycle"]
    with SessionLocal() as db:
        rows = db.query(DeviceReturnDebt).filter_by(device_id=seed["device_id"]).all()
        assert next(r for r in rows if r.family=="power-cycle").state == "closed"
        assert next(r for r in rows if r.family=="sleep").state == "open"
        assert db.query(DeviceLease).filter_by(device_id=seed["device_id"], status="ACTIVE").count() == 1


def test_discharge_wrong_token_precedes_probe_validation(client, seed, combination):
    response = client.post(f'/api/v1/agent/devices/{seed["device_id"]}/return-discharge', json={
        "fencing_token": "wrong-test-token", "family_results": {"power-cycle": result_entry(combination, "wrong_apk")}})
    assert response.status_code == 409
    with SessionLocal() as db:
        assert db.query(AuditLog).filter_by(action="unverified_probe_result").count() == 0
