"""PlanRun 设备执行矩阵（#1520 垂直切片：plan_runs /devices）。

``GET /plan-runs/{id}/devices``：Job×Device×Host×ACTIVE JOB lease 联表，
派生 ui_status / job_exec_status / link / busy / heartbeat deadline，
再按 status/link_status/host_id 过滤；facets 始终基于全集。

路由退化为计时包装 + ``_require_plan_run`` + ``ok(build_plan_run_devices(...))``。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from backend.api.schemas.plan_run import DeviceMatrixItem, PlanRunDevicesOut
from backend.core.job_timeout_config import (
    DISPATCHED_TIMEOUT_SECONDS,
    PATROL_STALL_MULTIPLIER,
    UNKNOWN_GRACE_SECONDS,
    running_heartbeat_timeout_seconds,
)
from backend.models.device_lease import DeviceLease
from backend.models.enums import DeviceStatus, HostStatus, JobStatus, LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRunHost
from backend.services.plan_run_queries import (
    derive_device_link_status,
    device_currently_disconnected,
)
from backend.services.plan_run_read_common import aware, iso

FAILED_JOB_STATUSES = {JobStatus.FAILED.value}

def job_exec_status_for_job(j: JobInstance, now: datetime) -> str:
    s = j.status
    if s == JobStatus.COMPLETED.value:
        return "completed"
    if s == JobStatus.ABORTED.value:
        return "aborted"
    if s in FAILED_JOB_STATUSES:
        return "failed"
    if s == JobStatus.PENDING.value:
        return "pending"
    if s == JobStatus.UNKNOWN.value:
        return "unknown"
    if (j.manual_action or "") == "EXIT_REQUESTED":
        return "backoff"
    nrt = aware(j.next_retry_at)
    if nrt and nrt > now:
        return "backoff"
    return "running"


def ui_status_for_job(
    j: JobInstance,
    now: datetime,
    device: Device | None,
    host_status: str | None,
) -> str:
    s = j.status
    if s == JobStatus.COMPLETED.value:
        return "completed"
    if s == JobStatus.ABORTED.value:
        return "aborted"
    if s in FAILED_JOB_STATUSES:
        return "failed"
    if s == JobStatus.PENDING.value:
        return "pending"

    if s == JobStatus.UNKNOWN.value:
        return "unknown"

    # RUNNING 分支
    disconnected = device_currently_disconnected(device, host_status)
    if disconnected:
        return "unknown"
    if (j.manual_action or "") == "EXIT_REQUESTED":
        return "backoff"
    nrt = aware(j.next_retry_at)
    if nrt and nrt > now:
        return "backoff"
    return "running"


def current_stage_for_job(j: JobInstance) -> str:
    s = j.status
    if s == JobStatus.COMPLETED.value:
        return "done"
    if s == JobStatus.UNKNOWN.value:
        return "unknown"
    if s == JobStatus.ABORTED.value:
        return "aborted"
    if s in FAILED_JOB_STATUSES:
        return "failed"
    if s == JobStatus.PENDING.value:
        return "pending"
    # RUNNING:有 patrol heartbeat 即视为 patrol;否则 init
    if (j.patrol_cycle_count or 0) > 0 or j.last_patrol_heartbeat_at:
        return "patrol"
    return "init"


def grace_remaining_seconds(j: JobInstance, now: datetime) -> Optional[int]:
    if j.status != JobStatus.UNKNOWN.value:
        return None
    ended = aware(j.ended_at)
    if ended is None:
        return None
    remaining = (ended + timedelta(seconds=UNKNOWN_GRACE_SECONDS) - now).total_seconds()
    return max(0, int(remaining))


def pending_claim_remaining_seconds(j: JobInstance, now: datetime) -> Optional[int]:
    if j.status != JobStatus.PENDING.value:
        return None
    base = aware(j.created_at) or aware(j.started_at)
    if base is None:
        return None
    remaining = (base + timedelta(seconds=DISPATCHED_TIMEOUT_SECONDS) - now).total_seconds()
    return max(0, int(remaining))


def pending_claim_deadline(j: JobInstance) -> Optional[datetime]:
    if j.status != JobStatus.PENDING.value:
        return None
    base = aware(j.created_at) or aware(j.started_at)
    if base is None:
        return None
    return base + timedelta(seconds=DISPATCHED_TIMEOUT_SECONDS)


def not_reported_liveness_anchor(j: JobInstance) -> Optional[datetime]:
    """Dispatch-time anchor for jobs whose liveness signal never arrived.

    Mirrors ``recycler._not_reported_anchor`` (#993 / ADR-0026 §3).
    """
    return aware(j.started_at) or aware(j.created_at)


# #993: 与 backend/scheduler/recycler.py 的判据常量保持同步（ADR-0026 §3）。
# recycler 是回收判死的唯一权威——本函数任何分支/超时改动必须同步
# recycler._running_liveness_anchor，反之亦然。
WAITING_EXECUTION_STATES = frozenset({
    "WAITING_EXECUTION_SLOT",
    "PATROL_SLEEP",
    "WAITING_BARRIER",
})
COORDINATOR_HEARTBEAT_TIMEOUT_SECONDS = int(
    os.getenv("COORDINATOR_HEARTBEAT_TIMEOUT_SECONDS", "300")
)


def running_heartbeat_deadline(
    j: JobInstance,
    coord_heartbeat_by_host: Optional[dict[str, datetime]] = None,
) -> Optional[datetime]:
    """Projected stuck deadline for a RUNNING job — aligned with
    ``recycler._running_liveness_anchor`` (#993 / ADR-0026 §3).

    ``updated_at`` is deliberately NOT a liveness signal: lease renewals must
    not be able to fake liveness (#288), so a job waiting for an execution
    slot (WAITING_EXECUTION_SLOT / PATROL_SLEEP / WAITING_BARRIER) stays
    non-stuck as long as its host's coordinator heartbeat is fresh — waiting
    is legal (invariant ②), only a dead coordinator counts.  Jobs whose
    liveness signal never arrived are anchored at dispatch time so they still
    age out after one full window.
    """
    if j.status != JobStatus.RUNNING.value:
        return None
    graded_timeout = running_heartbeat_timeout_seconds(
        j, patrol_stall_multiplier=PATROL_STALL_MULTIPLIER,
    )

    if j.execution_state == "EXECUTING_STEP":
        hb = aware(j.last_execution_heartbeat_at)
        if hb is None:
            hb = not_reported_liveness_anchor(j)
        if hb is None:
            return None
        return hb + timedelta(seconds=graded_timeout)

    if j.execution_state in WAITING_EXECUTION_STATES:
        hb = None
        if coord_heartbeat_by_host is not None:
            hb = coord_heartbeat_by_host.get(j.host_id)
        if hb is None:
            hb = not_reported_liveness_anchor(j)
        if hb is None:
            return None
        return hb + timedelta(seconds=COORDINATOR_HEARTBEAT_TIMEOUT_SECONDS)

    # NULL / unknown execution_state — nothing ever reported.
    hb = not_reported_liveness_anchor(j)
    if hb is None:
        return None
    return hb + timedelta(seconds=graded_timeout)


def adb_state_excluded(adb_state: str | None) -> bool:
    """Match claim pre-filter: offline/unknown adb_state excludes the device."""
    state = (adb_state or "device").lower()
    return state in ("offline", "unknown")


def derive_busy_reason(
    device: Device | None,
    host_status: str | None,
    lease_job_id: int | None,
) -> tuple[Optional[str], Optional[int]]:
    if device is None:
        return None, None
    if host_status == HostStatus.OFFLINE.value:
        return "host_offline", lease_job_id
    if adb_state_excluded(device.adb_state):
        return "adb_excluded", lease_job_id
    if not device.adb_connected or device.status == DeviceStatus.OFFLINE.value:
        return "device_offline", lease_job_id
    if lease_job_id is not None:
        return "active_lease", lease_job_id
    if device.status == DeviceStatus.BUSY.value:
        return "active_lease", None
    return None, None


def build_plan_run_devices(
    db: Session,
    run_id: int,
    *,
    status: Optional[str] = None,
    link_status: Optional[str] = None,
    host_id: Optional[str] = None,
) -> PlanRunDevicesOut:
    # ADR-0026 P2-3: single JOIN for jobs + device + host + ACTIVE JOB lease
    # (was 4 sequential SELECTs; matters at ~1000 devices / PlanRun).
    joined = db.execute(
        select(
            JobInstance,
            Device,
            Host.status.label("host_status"),
            DeviceLease.job_id.label("lease_job_id"),
        )
        .select_from(JobInstance)
        .outerjoin(Device, Device.id == JobInstance.device_id)
        .outerjoin(Host, Host.id == JobInstance.host_id)
        .outerjoin(
            DeviceLease,
            and_(
                DeviceLease.device_id == JobInstance.device_id,
                DeviceLease.status == LeaseStatus.ACTIVE.value,
                DeviceLease.lease_type == LeaseType.JOB.value,
            ),
        )
        .where(JobInstance.plan_run_id == run_id)
        .order_by(JobInstance.device_id.asc(), JobInstance.id.asc())
    ).all()
    if not joined:
        return PlanRunDevicesOut(
            plan_run_id=run_id, total=0,
            by_status={"all": 0}, by_link_status={"all": 0},
            by_host={}, devices=[],
        )

    now = datetime.now(timezone.utc)
    items: list[DeviceMatrixItem] = []
    by_status: dict[str, int] = {"all": 0}
    by_link_status: dict[str, int] = {"all": 0}
    by_host: dict[str, int] = {}

    # #993: WAITING_* 存活判据需要每 host coordinator 心跳（recycler 同源）。
    coord_rows = db.execute(
        select(PlanRunHost.host_id, PlanRunHost.coordinator_heartbeat_at)
        .where(PlanRunHost.plan_run_id == run_id)
    ).all()
    coord_heartbeat_by_host: dict[str, datetime] = {
        host_id: aware(hb)
        for host_id, hb in coord_rows
        if hb is not None
    }

    for j, dev, host_st, lease_job_id in joined:
        serial = dev.serial if dev else None
        model = dev.model if dev else None
        ui = ui_status_for_job(j, now, dev, host_st)
        cur_stage = current_stage_for_job(j)
        pending_deadline = pending_claim_deadline(j)
        heartbeat_deadline = running_heartbeat_deadline(
            j, coord_heartbeat_by_host,
        )
        busy_reason, busy_lease_job_id = derive_busy_reason(dev, host_st, lease_job_id)
        link = derive_device_link_status(dev, host_st)
        exec_status = job_exec_status_for_job(j, now)
        disconnected = device_currently_disconnected(dev, host_st)
        manual_retry_allowed = (
            j.status == JobStatus.RUNNING.value and not disconnected
        )
        items.append(DeviceMatrixItem(
            device_id=j.device_id,
            device_serial=serial,
            device_model=model,
            host_id=j.host_id,
            job_id=j.id,
            job_status=j.status,
            ui_status=ui,
            current_stage=cur_stage,
            current_step=j.current_patrol_step,
            patrol_cycle_count=j.patrol_cycle_count or 0,
            patrol_success_cycle_count=j.patrol_success_cycle_count or 0,
            patrol_failed_cycle_count=j.patrol_failed_cycle_count or 0,
            current_failure_streak=j.current_failure_streak or 0,
            next_retry_at=iso(j.next_retry_at),
            manual_action=j.manual_action,
            log_signal_count=j.log_signal_count or 0,
            last_heartbeat_at=iso(j.last_patrol_heartbeat_at),
            started_at=iso(j.started_at),
            created_at=iso(j.created_at),
            ended_at=iso(j.ended_at),
            status_reason=j.status_reason,
            grace_remaining_seconds=grace_remaining_seconds(j, now),
            pending_claim_remaining_seconds=pending_claim_remaining_seconds(j, now),
            pending_claim_deadline_at=iso(pending_deadline),
            heartbeat_deadline_at=iso(heartbeat_deadline),
            is_stuck=bool(heartbeat_deadline and now >= heartbeat_deadline),
            busy_reason=busy_reason,
            busy_lease_job_id=busy_lease_job_id,
            device_link_status=link,
            job_exec_status=exec_status,
            adb_state=dev.adb_state if dev else None,
            adb_connected=dev.adb_connected if dev else None,
            capabilities={
                "manual_retry": manual_retry_allowed,
                "manual_exit": j.status == JobStatus.RUNNING.value,
                "manual_retry_blocked_reason": (
                    "device_disconnected"
                    if j.status == JobStatus.RUNNING.value and disconnected
                    else None
                ),
                "open_report": j.status in {
                    JobStatus.COMPLETED.value,
                    JobStatus.FAILED.value,
                    JobStatus.ABORTED.value,
                },
            },
        ))
        by_status["all"] += 1
        by_status[exec_status] = by_status.get(exec_status, 0) + 1
        by_link_status["all"] += 1
        by_link_status[link] = by_link_status.get(link, 0) + 1
        if j.host_id:
            by_host[j.host_id] = by_host.get(j.host_id, 0) + 1

    # 过滤(facets 已经基于全集)
    filtered = items
    if status and status.lower() != "all":
        filtered = [d for d in filtered if d.job_exec_status == status.lower()]
    if link_status and link_status.lower() != "all":
        filtered = [d for d in filtered if d.device_link_status == link_status.lower()]
    if host_id and host_id.lower() != "all":
        filtered = [d for d in filtered if d.host_id == host_id]

    return PlanRunDevicesOut(
        plan_run_id=run_id,
        total=len(items),
        by_status=by_status,
        by_link_status=by_link_status,
        by_host=by_host,
        devices=filtered,
    )


# 路由 / 既有测试用的私有名别名。
_job_exec_status_for_job = job_exec_status_for_job
_ui_status_for_job = ui_status_for_job
_current_stage_for_job = current_stage_for_job
_grace_remaining_seconds = grace_remaining_seconds
_pending_claim_remaining_seconds = pending_claim_remaining_seconds
_pending_claim_deadline = pending_claim_deadline
_not_reported_liveness_anchor = not_reported_liveness_anchor
_running_heartbeat_deadline = running_heartbeat_deadline
_adb_state_excluded = adb_state_excluded
_derive_busy_reason = derive_busy_reason
_get_plan_run_devices_impl = build_plan_run_devices
_FAILED_JOB_STATUSES = FAILED_JOB_STATUSES
_WAITING_EXECUTION_STATES = WAITING_EXECUTION_STATES
_COORDINATOR_HEARTBEAT_TIMEOUT_SECONDS = COORDINATOR_HEARTBEAT_TIMEOUT_SECONDS
