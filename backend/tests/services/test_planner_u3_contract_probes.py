"""#3723 U3 / §3.4.1：Planner 隔离 PG 四反例（修前红 / 修后绿）。"""

from __future__ import annotations

from backend.tests.disarm_probe_evidence import trust_test_probe
from datetime import datetime, timezone

from backend.models.device_lease import DeviceLease
from backend.models.device_return_debt import DeviceReturnDebt
from backend.models.job import StepTrace
from backend.services import device_return as dr
from backend.services.device_lease_emergency_release import release_terminal_job_lease
from backend.tests.services.test_device_return_u3_3728 import _seed


def test_no_terminal_report_with_started_footprint_must_keep_debt(db_session):
    device, job, lease, _ = _seed(db_session, disarm_results={})
    job.disarm_results = None
    job.pipeline_def = {
        "lifecycle": {
            "init": [{"step_id": "setup", "action": "script:powercycle_setup"}],
            "teardown": [{"step_id": "finish", "action": "script:powercycle_finish"}],
        }
    }
    db_session.add(
        StepTrace(
            job_id=job.id,
            step_id="setup",
            stage="init",
            status="RUNNING",
            event_type="STARTED",
            original_ts=datetime.now(timezone.utc),
        )
    )
    db_session.commit()
    release_terminal_job_lease(
        db_session,
        device_id=device.id,
        lease_id=lease.id,
        reason="isolated probe",
        user_id=None,
        username="planner",
    )
    db_session.refresh(lease)
    assert dr.is_debt_lease(lease), {"type": lease.lease_type, "status": lease.status}
    assert (
        db_session.query(DeviceReturnDebt)
        .filter_by(device_id=device.id, state="open")
        .count()
        == 1
    )


def test_pending_without_footprint_still_releases(db_session):
    """无足迹 PENDING：不得一律造欠账，仍应释放 JOB 租约。"""
    device, job, lease, _ = _seed(db_session, disarm_results={})
    job.disarm_results = None
    job.pipeline_def = {"lifecycle": {"init": [], "teardown": []}}
    db_session.commit()
    release_terminal_job_lease(
        db_session,
        device_id=device.id,
        lease_id=lease.id,
        reason="pending no footprint",
        user_id=None,
        username="planner",
    )
    db_session.refresh(lease)
    assert lease.status == "RELEASED"
    assert lease.lease_type == "JOB"
    assert (
        db_session.query(DeviceReturnDebt)
        .filter_by(device_id=device.id, state="open")
        .count()
        == 0
    )


def test_init_onboarded_latest_failed_job_creates_stock_debt(db_session, monkeypatch):
    device, job, lease, _ = _seed(
        db_session, disarm_results={"power-cycle": {"result": "not_executed"}}
    )
    job.pipeline_def = {
        "lifecycle": {
            "init": [{"step_id": "setup", "action": "script:powercycle_setup"}],
            "teardown": [],
        }
    }
    db_session.add(
        StepTrace(
            job_id=job.id,
            step_id="setup",
            stage="init",
            status="RUNNING",
            event_type="STARTED",
            original_ts=datetime.now(timezone.utc),
        )
    )
    lease.status = "RELEASED"
    device.return_state = None
    db_session.commit()
    monkeypatch.setattr(
        dr, "ONBOARDED", {"power-cycle": {"verified_device_classes": []}}
    )
    result = dr.init_device_return_sync(db_session, device_id=device.id, dry_run=False)
    db_session.commit()
    db_session.refresh(device)
    assert device.return_state["power-cycle"]["state"] == "debt", result
    assert (
        db_session.query(DeviceReturnDebt)
        .filter_by(
            device_id=device.id,
            state="open",
            source_kind="stock",
            source_job_id=job.id,
        )
        .count()
        == 1
    )
    assert (
        db_session.query(DeviceLease)
        .filter_by(device_id=device.id, status="ACTIVE")
        .count()
        == 1
    )


def test_existing_open_debt_cannot_be_overwritten_as_unverified(db_session):
    device, job, lease, _ = _seed(
        db_session, disarm_results={"power-cycle": {"result": "executed_unverified"}}
    )
    device.return_state = {"power-cycle": {"state": "debt", "source": "stock"}}
    db_session.add(
        DeviceReturnDebt(
            device_id=device.id,
            family="power-cycle",
            source_kind="stock",
            source_job_id=None,
            cause="incomplete",
            state="open",
            evidence={},
        )
    )
    db_session.commit()
    dr.handle_job_lease_release_sync(
        db_session, device_id=device.id, job_id=job.id, job=job
    )
    db_session.commit()
    db_session.refresh(device)
    assert (
        db_session.query(DeviceReturnDebt)
        .filter_by(device_id=device.id, family="power-cycle", state="open")
        .count()
        == 1
    )
    assert device.return_state["power-cycle"]["state"] == "debt", device.return_state


def test_real_late_complete_closes_same_source_without_flipping_job(client, db_session, monkeypatch):
    device, job, lease, _ = _seed(db_session)
    trusted = trust_test_probe(db_session, monkeypatch, device.id, job.id)
    job.status = "FAILED"
    token = lease.fencing_token
    db_session.commit()
    dr.handle_job_lease_release_sync(
        db_session, device_id=device.id, job_id=job.id, job=job
    )
    db_session.commit()
    response = client.post(
        f"/api/v1/agent/jobs/{job.id}/complete",
        json={
            "update": {"status": "FAILED"},
            "fencing_token": token,
            "disarm_results": {"power-cycle": trusted},
        },
    )
    assert response.status_code == 200, response.text
    db_session.refresh(job)
    assert job.status == "FAILED"
    assert (
        db_session.query(DeviceReturnDebt)
        .filter_by(device_id=device.id, state="open", source_job_id=job.id)
        .count()
        == 0
    )


def test_late_complete_wrong_token_still_409(client, db_session):
    """负向：错误 token 不得借迟到 confirmed 放宽 STALE 规则。"""
    device, job, lease, _ = _seed(db_session)
    job.status = "FAILED"
    db_session.commit()
    dr.handle_job_lease_release_sync(
        db_session, device_id=device.id, job_id=job.id, job=job
    )
    db_session.commit()
    response = client.post(
        f"/api/v1/agent/jobs/{job.id}/complete",
        json={
            "update": {"status": "FAILED"},
            "fencing_token": "not-the-original-token",
            "disarm_results": {"power-cycle": {"result": "confirmed"}},
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "STALE_COMPLETION_TOKEN"
