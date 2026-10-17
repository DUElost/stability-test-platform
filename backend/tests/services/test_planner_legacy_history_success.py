"""#3723 U3 / §3.4.1.2：预阶段 A 历史 job 无 disarm_results 时从 teardown 推导。"""

from __future__ import annotations

from datetime import timedelta

from backend.models.device_lease import DeviceLease
from backend.models.device_return_debt import DeviceReturnDebt
from backend.models.job import JobInstance, StepTrace
from backend.models.enums import JobStatus, LeaseStatus, LeaseType
from backend.services import device_return as dr
from backend.tests.services.test_device_return_u3_3728 import _seed


def _add_later_job(db, *, older, host, device, status, pipeline_def, ended_at, disarm_results=None):
    """Same device, later ended_at, but a separate PlanRun (uq_job_instance_plan_run_device)."""
    from backend.models.plan_run import PlanRun

    run = PlanRun(
        plan_id=older.plan_id,
        status="RUNNING",
        plan_snapshot={"name": "later"},
        run_type="MANUAL",
        triggered_by="test",
    )
    db.add(run)
    db.flush()
    job = JobInstance(
        plan_run_id=run.id,
        plan_id=older.plan_id,
        device_id=device.id,
        host_id=host.id,
        status=status,
        pipeline_def=pipeline_def,
        ended_at=ended_at,
        disarm_results=disarm_results,
    )
    db.add(job)
    db.flush()
    return job



def test_pre_stage_a_successful_teardown_is_not_initialized_as_debt(
    db_session, monkeypatch
):
    device, job, lease, _ = _seed(db_session, disarm_results={})
    # Pre-stage-A historical job: migration added nullable column, no backfill.
    job.disarm_results = None
    job.pipeline_def = {
        "lifecycle": {
            "init": [{"step_id": "setup", "action": "script:powercycle_setup"}],
            "teardown": [{"step_id": "finish", "action": "script:powercycle_finish"}],
        }
    }
    db_session.add_all(
        [
            StepTrace(
                job_id=job.id,
                step_id="setup",
                stage="init",
                status="RUNNING",
                event_type="STARTED",
                original_ts=job.ended_at - timedelta(seconds=60),
            ),
            StepTrace(
                job_id=job.id,
                step_id="finish",
                stage="teardown",
                status="COMPLETED",
                event_type="COMPLETED",
                exit_code=0,
                original_ts=job.ended_at - timedelta(seconds=1),
            ),
        ]
    )
    lease.status = "RELEASED"
    device.return_state = None
    db_session.commit()
    monkeypatch.setattr(
        dr, "ONBOARDED", {"power-cycle": {"verified_device_classes": []}}
    )
    summary = dr.init_device_return_sync(db_session, device_id=device.id, dry_run=False)
    db_session.commit()
    db_session.refresh(device)
    assert device.return_state["power-cycle"]["state"] == "unverified", summary
    assert (
        db_session.query(DeviceReturnDebt)
        .filter_by(device_id=device.id, state="open")
        .count()
        == 0
    )
    assert (
        db_session.query(DeviceLease)
        .filter_by(device_id=device.id, status="ACTIVE")
        .count()
        == 0
    )


def test_later_successful_teardown_covers_earlier_residue(db_session, monkeypatch):
    """后续成功解除覆盖更早残留：最新 job 推导为 executed_unverified → unverified。"""
    device, older, lease, host = _seed(
        db_session, disarm_results={"power-cycle": {"result": "not_executed"}}
    )
    older.pipeline_def = {
        "lifecycle": {
            "init": [{"step_id": "setup", "action": "script:powercycle_setup"}],
            "teardown": [],
        }
    }
    db_session.add(
        StepTrace(
            job_id=older.id,
            step_id="setup",
            stage="init",
            status="RUNNING",
            event_type="STARTED",
            original_ts=older.ended_at - timedelta(seconds=30),
        )
    )
    lease.status = "RELEASED"

    newer = _add_later_job(
        db_session,
        older=older,
        host=host,
        device=device,
        status=JobStatus.COMPLETED.value,
        pipeline_def={
            "lifecycle": {
                "init": [{"step_id": "setup", "action": "script:powercycle_setup"}],
                "teardown": [
                    {"step_id": "finish", "action": "script:powercycle_finish"}
                ],
            }
        },
        ended_at=older.ended_at + timedelta(hours=1),
        disarm_results=None,
    )
    db_session.add_all(
        [
            StepTrace(
                job_id=newer.id,
                step_id="setup",
                stage="init",
                status="RUNNING",
                event_type="STARTED",
                original_ts=newer.ended_at - timedelta(seconds=60),
            ),
            StepTrace(
                job_id=newer.id,
                step_id="finish",
                stage="teardown",
                status="COMPLETED",
                event_type="COMPLETED",
                exit_code=0,
                original_ts=newer.ended_at - timedelta(seconds=1),
            ),
        ]
    )
    device.return_state = None
    db_session.commit()
    monkeypatch.setattr(
        dr, "ONBOARDED", {"power-cycle": {"verified_device_classes": []}}
    )
    summary = dr.init_device_return_sync(db_session, device_id=device.id, dry_run=False)
    db_session.commit()
    db_session.refresh(device)
    assert device.return_state["power-cycle"]["state"] == "unverified", summary
    assert device.return_state["power-cycle"].get("history_job_id") == newer.id
    assert (
        db_session.query(DeviceReturnDebt)
        .filter_by(device_id=device.id, state="open")
        .count()
        == 0
    )


def test_later_failed_teardown_not_shadowed_by_earlier_success(db_session, monkeypatch):
    """后续失败不能被旧成功遮蔽：最新 job 推导 failed → debt。"""
    device, older, lease, host = _seed(db_session, disarm_results={})
    older.disarm_results = None
    older.pipeline_def = {
        "lifecycle": {
            "init": [{"step_id": "setup", "action": "script:powercycle_setup"}],
            "teardown": [{"step_id": "finish", "action": "script:powercycle_finish"}],
        }
    }
    db_session.add_all(
        [
            StepTrace(
                job_id=older.id,
                step_id="setup",
                stage="init",
                status="RUNNING",
                event_type="STARTED",
                original_ts=older.ended_at - timedelta(seconds=60),
            ),
            StepTrace(
                job_id=older.id,
                step_id="finish",
                stage="teardown",
                status="COMPLETED",
                event_type="COMPLETED",
                exit_code=0,
                original_ts=older.ended_at - timedelta(seconds=1),
            ),
        ]
    )
    lease.status = "RELEASED"

    newer = _add_later_job(
        db_session,
        older=older,
        host=host,
        device=device,
        status=JobStatus.FAILED.value,
        pipeline_def={
            "lifecycle": {
                "init": [{"step_id": "setup", "action": "script:powercycle_setup"}],
                "teardown": [
                    {"step_id": "finish", "action": "script:powercycle_finish"}
                ],
            }
        },
        ended_at=older.ended_at + timedelta(hours=1),
        disarm_results=None,
    )
    db_session.add_all(
        [
            StepTrace(
                job_id=newer.id,
                step_id="setup",
                stage="init",
                status="RUNNING",
                event_type="STARTED",
                original_ts=newer.ended_at - timedelta(seconds=60),
            ),
            StepTrace(
                job_id=newer.id,
                step_id="finish",
                stage="teardown",
                status="FAILED",
                event_type="FAILED",
                exit_code=1,
                original_ts=newer.ended_at - timedelta(seconds=1),
            ),
        ]
    )
    device.return_state = None
    db_session.commit()
    monkeypatch.setattr(
        dr, "ONBOARDED", {"power-cycle": {"verified_device_classes": []}}
    )
    summary = dr.init_device_return_sync(db_session, device_id=device.id, dry_run=False)
    db_session.commit()
    db_session.refresh(device)
    assert device.return_state["power-cycle"]["state"] == "debt", summary
    assert (
        db_session.query(DeviceReturnDebt)
        .filter_by(
            device_id=device.id,
            state="open",
            source_kind="stock",
            source_job_id=newer.id,
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
