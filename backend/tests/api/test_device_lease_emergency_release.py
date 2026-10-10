"""#3646：管理员紧急释放终态 job 的 ACTIVE JOB 租约。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from backend.models.audit import AuditLog
from backend.models.device_lease import DeviceLease
from backend.models.enums import JobStatus, LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun

_NOW = datetime.now(timezone.utc)


def _release(client, headers, device_id: int, lease_id: int, reason: str = "回收器停了"):
    return client.post(
        f"/api/v1/devices/{device_id}/leases/{lease_id}/release",
        json={"reason": reason},
        headers=headers,
    )


def _seed(db, *, job_status: str, lease_status: str = "ACTIVE", lease_type: str = "JOB"):
    suffix = uuid4().hex[:8]
    host = Host(
        id=f"rel-{suffix}",
        hostname=f"rel-{suffix}",
        status="ONLINE",
        last_heartbeat=_NOW,
        created_at=_NOW,
    )
    device = Device(
        serial=f"SER-{suffix}",
        host_id=host.id,
        status="BUSY",
        tags=[],
        created_at=_NOW,
        last_seen=_NOW,
    )
    plan = Plan(name=f"plan-{suffix}", description="emergency release", created_by="test")
    db.add_all([host, device, plan])
    db.flush()
    run = PlanRun(
        plan_id=plan.id,
        status="RUNNING",
        plan_snapshot={"name": plan.name},
        run_type="MANUAL",
        triggered_by="test",
    )
    db.add(run)
    db.flush()
    job = JobInstance(
        plan_run_id=run.id,
        plan_id=plan.id,
        device_id=device.id,
        host_id=host.id,
        status=job_status,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        ended_at=_NOW if job_status in {"COMPLETED", "FAILED", "ABORTED"} else None,
    )
    db.add(job)
    db.flush()
    lease = DeviceLease(
        device_id=device.id,
        job_id=job.id,
        host_id=host.id,
        lease_type=lease_type,
        status=lease_status,
        fencing_token=f"{suffix}:1",
        lease_generation=1,
        agent_instance_id=f"agent-{suffix}",
        acquired_at=_NOW,
        renewed_at=_NOW,
        expires_at=_NOW + timedelta(hours=1),
        released_at=_NOW if lease_status == "RELEASED" else None,
    )
    db.add(lease)
    db.commit()
    return device, job, run, lease


def _audit(db, device_id: int) -> AuditLog | None:
    db.expire_all()
    return db.execute(
        select(AuditLog).where(
            AuditLog.resource_type == "device",
            AuditLog.resource_id == str(device_id),
            AuditLog.action == "emergency_release_lease",
        )
    ).scalars().first()


class TestEmergencyRelease:
    @pytest.mark.parametrize("job_status", [
        JobStatus.COMPLETED.value,
        JobStatus.FAILED.value,
        JobStatus.ABORTED.value,
    ])
    def test_terminal_job_releases_and_audits(self, client, db_session, admin_headers, job_status):
        device, job, run, lease = _seed(db_session, job_status=job_status)
        response = _release(client, admin_headers, device.id, lease.id, reason="回收器不可用")
        assert response.status_code == 200, response.text
        body = response.json()["data"]
        assert body["status"] == LeaseStatus.RELEASED.value
        assert body["lease_id"] == lease.id
        assert body["job_id"] == job.id
        assert body["device_id"] == device.id

        db_session.expire_all()
        assert db_session.get(DeviceLease, lease.id).status == LeaseStatus.RELEASED.value
        assert db_session.get(DeviceLease, lease.id).released_at is not None
        assert db_session.get(JobInstance, job.id).status == job_status
        assert db_session.get(PlanRun, run.id).status == "RUNNING"

        entry = _audit(db_session, device.id)
        assert entry is not None
        assert entry.username == "admin"
        assert entry.details["lease_id"] == lease.id
        assert entry.details["job_id"] == job.id
        assert entry.details["job_status"] == job_status
        assert entry.details["reason"] == "回收器不可用"
        assert entry.details["operator"] == "admin"

    @pytest.mark.parametrize("job_status", [
        JobStatus.RUNNING.value,
        JobStatus.PENDING.value,
        JobStatus.UNKNOWN.value,
    ])
    def test_non_terminal_job_conflicts(self, client, db_session, admin_headers, job_status):
        device, job, _run, lease = _seed(db_session, job_status=job_status)
        response = _release(client, admin_headers, device.id, lease.id)
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "JOB_NOT_TERMINAL"
        db_session.expire_all()
        assert db_session.get(DeviceLease, lease.id).status == LeaseStatus.ACTIVE.value
        assert db_session.get(JobInstance, job.id).status == job_status
        assert _audit(db_session, device.id) is None

    def test_non_job_lease_conflicts(self, client, db_session, admin_headers):
        device, _job, _run, lease = _seed(
            db_session,
            job_status=JobStatus.COMPLETED.value,
            lease_type=LeaseType.MAINTENANCE.value,
        )
        response = _release(client, admin_headers, device.id, lease.id)
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "LEASE_TYPE_NOT_JOB"
        db_session.expire_all()
        assert db_session.get(DeviceLease, lease.id).status == LeaseStatus.ACTIVE.value

    def test_released_lease_conflicts(self, client, db_session, admin_headers):
        device, _job, _run, lease = _seed(
            db_session,
            job_status=JobStatus.COMPLETED.value,
            lease_status=LeaseStatus.RELEASED.value,
        )
        response = _release(client, admin_headers, device.id, lease.id)
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "LEASE_NOT_ACTIVE"
        db_session.expire_all()
        assert db_session.get(DeviceLease, lease.id).status == LeaseStatus.RELEASED.value

    def test_device_mismatch_is_404(self, client, db_session, admin_headers):
        device, _job, _run, lease = _seed(db_session, job_status=JobStatus.COMPLETED.value)
        other = Device(
            serial=f"OTHER-{uuid4().hex[:8]}",
            host_id=device.host_id,
            status="ONLINE",
            tags=[],
            created_at=_NOW,
            last_seen=_NOW,
        )
        db_session.add(other)
        db_session.commit()
        response = _release(client, admin_headers, other.id, lease.id)
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "LEASE_DEVICE_MISMATCH"
        db_session.expire_all()
        assert db_session.get(DeviceLease, lease.id).status == LeaseStatus.ACTIVE.value

    def test_missing_lease_is_404(self, client, db_session, admin_headers):
        device, _job, _run, _lease = _seed(db_session, job_status=JobStatus.COMPLETED.value)
        response = _release(client, admin_headers, device.id, 9_999_999)
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "LEASE_NOT_FOUND"

    def test_non_admin_forbidden(self, client, db_session, auth_headers):
        device, _job, _run, lease = _seed(db_session, job_status=JobStatus.COMPLETED.value)
        response = _release(client, auth_headers, device.id, lease.id)
        assert response.status_code == 403
        db_session.expire_all()
        assert db_session.get(DeviceLease, lease.id).status == LeaseStatus.ACTIVE.value
        assert _audit(db_session, device.id) is None

    def test_blank_reason_rejected(self, client, db_session, admin_headers):
        device, _job, _run, lease = _seed(db_session, job_status=JobStatus.COMPLETED.value)
        response = _release(client, admin_headers, device.id, lease.id, reason="   ")
        assert response.status_code == 422
        db_session.expire_all()
        assert db_session.get(DeviceLease, lease.id).status == LeaseStatus.ACTIVE.value

    def test_other_jobs_active_lease_is_not_released(self, client, db_session, admin_headers):
        """旧租约已释放后，同设备上另一个 job 的新 ACTIVE 租约保持不动。"""
        device, old_job, _run, old_lease = _seed(
            db_session,
            job_status=JobStatus.COMPLETED.value,
            lease_status=LeaseStatus.RELEASED.value,
        )
        new_plan = Plan(name=f"plan-new-{uuid4().hex[:8]}", created_by="test")
        db_session.add(new_plan)
        db_session.flush()
        new_run = PlanRun(
            plan_id=new_plan.id,
            status="RUNNING",
            plan_snapshot={},
            run_type="MANUAL",
            triggered_by="test",
        )
        db_session.add(new_run)
        db_session.flush()
        new_job = JobInstance(
            plan_run_id=new_run.id,
            plan_id=new_plan.id,
            device_id=device.id,
            host_id=device.host_id,
            status=JobStatus.RUNNING.value,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        )
        db_session.add(new_job)
        db_session.flush()
        new_lease = DeviceLease(
            device_id=device.id,
            job_id=new_job.id,
            host_id=device.host_id,
            lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token="new:1",
            lease_generation=2,
            agent_instance_id="agent-new",
            acquired_at=_NOW,
            renewed_at=_NOW,
            expires_at=_NOW + timedelta(hours=1),
        )
        db_session.add(new_lease)
        db_session.commit()

        response = _release(client, admin_headers, device.id, old_lease.id)
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "LEASE_NOT_ACTIVE"
        db_session.expire_all()
        assert db_session.get(DeviceLease, new_lease.id).status == LeaseStatus.ACTIVE.value
        assert db_session.get(DeviceLease, old_lease.id).status == LeaseStatus.RELEASED.value
        assert db_session.get(JobInstance, new_job.id).status == JobStatus.RUNNING.value
        assert db_session.get(JobInstance, old_job.id).status == JobStatus.COMPLETED.value
