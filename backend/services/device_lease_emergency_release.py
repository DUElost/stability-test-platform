"""管理员紧急释放一条终态 job 的 ACTIVE JOB 租约（#3646）。

与回收器 D5 同一判定：只释放 ``TERMINAL_JOB_STATUSES`` 上的 JOB 租约，
不改 job / PlanRun。匹配键是 ``release_lease_sync`` 的
``device_id + job_id + lease_type + ACTIVE``，所以同一设备上属于其他 job
的新 ACTIVE 租约不会被这条语句碰到。

锁序（I1）：先读出租约的 ``job_id``（不加锁），再 ``job_instance FOR UPDATE``，
再 ``device_leases FOR UPDATE``。持有租约行锁之后不得再去锁另一条 job 行。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.device_lease import DeviceLease
from backend.models.enums import TERMINAL_JOB_STATUSES, LeaseStatus, LeaseType
from backend.models.job import JobInstance
from backend.services.audit_writer import record_audit
from backend.services.errors import Conflict, NotFound
from backend.services.lease_manager import release_lease_sync


@dataclass(frozen=True)
class ReleasedLease:
    lease_id: int
    device_id: int
    job_id: int
    status: str


def release_terminal_job_lease(
    db: Session,
    *,
    device_id: int,
    lease_id: int,
    reason: str,
    user_id: Optional[int],
    username: Optional[str],
    request: Optional[Any] = None,
) -> ReleasedLease:
    snapshot = db.execute(
        select(DeviceLease.id, DeviceLease.device_id, DeviceLease.job_id).where(
            DeviceLease.id == lease_id
        )
    ).one_or_none()
    if snapshot is None:
        raise NotFound({"code": "LEASE_NOT_FOUND", "message": "租约不存在"})
    if snapshot.device_id != device_id:
        raise NotFound({"code": "LEASE_DEVICE_MISMATCH", "message": "租约不属于路径中的设备"})

    locked_job_id = snapshot.job_id
    job: JobInstance | None = None
    if locked_job_id is not None:
        job = db.execute(
            select(JobInstance)
            .where(JobInstance.id == locked_job_id)
            .with_for_update()
        ).scalar_one_or_none()

    lease = db.execute(
        select(DeviceLease)
        .where(DeviceLease.id == lease_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if lease is None:
        raise NotFound({"code": "LEASE_NOT_FOUND", "message": "租约不存在"})
    if lease.device_id != device_id:
        raise NotFound({"code": "LEASE_DEVICE_MISMATCH", "message": "租约不属于路径中的设备"})
    if lease.job_id != locked_job_id:
        raise Conflict({
            "code": "LEASE_JOB_CHANGED",
            "message": "租约的 job 在加锁期间变了，未释放",
        })
    if lease.status != LeaseStatus.ACTIVE.value:
        raise Conflict({"code": "LEASE_NOT_ACTIVE", "message": "租约不是 ACTIVE"})
    if lease.lease_type != LeaseType.JOB.value:
        raise Conflict({"code": "LEASE_TYPE_NOT_JOB", "message": "租约类型不是 JOB"})
    if job is None or job.status not in TERMINAL_JOB_STATUSES:
        raise Conflict({"code": "JOB_NOT_TERMINAL", "message": "关联 job 不是终态"})

    released = release_lease_sync(db, device_id, job.id, LeaseType.JOB)
    if not released:
        raise Conflict({"code": "LEASE_RELEASE_LOST", "message": "租约未释放"})

    record_audit(
        db,
        action="emergency_release_lease",
        resource_type="device",
        resource_id=device_id,
        details={
            "lease_id": lease.id,
            "job_id": job.id,
            "job_status": job.status,
            "reason": reason,
            "operator": username,
        },
        user_id=user_id,
        username=username,
        request=request,
        strict=True,
    )
    db.commit()
    return ReleasedLease(
        lease_id=lease.id,
        device_id=device_id,
        job_id=job.id,
        status=LeaseStatus.RELEASED.value,
    )
