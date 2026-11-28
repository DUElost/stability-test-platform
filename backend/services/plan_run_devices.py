"""PlanRun 设备执行矩阵、轻量全集 Minimap 与真分页（#1520 / #83 / #4117 B2-3）。

- ``GET /plan-runs/{id}/devices/minimap``：直接以窄投影构建全量 7 字段设备格子、
  全集 facets、按主机去重显示名映射（含退役机与无主机桶）与全局 ``stuck_job_ids``，
  绝不先构造重 ``DeviceMatrixItem`` 再裁剪；
- ``GET /plan-runs/{id}/devices``：保留不传分页参数返回全量与既有 ``total``（PlanRun 全集数）
  的向后兼容行为；支持 ``skip`` / ``limit`` / ``q`` / ``sort_by`` / ``sort_dir`` 与聚合 KPI 并集筛选
  （``status=lost_or_backoff`` / 逗号分隔并集；``link_status=abnormal`` / ``not_online`` / 逗号分隔），
  新增 ``filtered_total`` 不覆盖 ``total``，且仅对当前页构造重 ``DeviceMatrixItem``。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from backend.api.schemas.plan_run import (
    DeviceMatrixItem,
    PlanRunDevicesMinimapOut,
    PlanRunDevicesOut,
    PlanRunMinimapDeviceOut,
    PlanRunMinimapFacetsOut,
)
from backend.agent.contracts.device_return import (
    family_for_footprint_script,
    is_disarm_role,
    script_name_from_step,
    select_disarm_steps,
    step_teardown_role,
    step_version_of,
)
from backend.agent.contracts.finishing_guard import (
    ATOMIC_LIVENESS_FRESH_SECONDS,
    FINISHING_KIND_ATOMIC_COMMIT,
    FINISHING_KIND_TEARDOWN_DISARM,
    FINISHING_TRIGGER_MANUAL_ABORT,
    FINISHING_TRIGGER_MANUAL_EXIT,
    FINISHING_TRIGGER_NATURAL_END,
    FINISHING_TRIGGERS,
    GUARD_STATE_GRANTED,
    coerce_job_finishing_guard,
    effective_abort_force,
    finishing_kind_of,
    is_clock_regressed,
)
from backend.core.job_timeout_config import (
    DISPATCHED_TIMEOUT_SECONDS,
    EXECUTING_DISARM,
    HOST_HEARTBEAT_TIMEOUT_SECONDS,
    PATROL_STALL_MULTIPLIER,
    UNKNOWN_GRACE_SECONDS,
    running_heartbeat_timeout_seconds,
)
from backend.models.device_lease import DeviceLease
from backend.models.enums import DeviceStatus, HostStatus, JobStatus, LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.job import JobInstance, StepTrace
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun, PlanRunHost
from backend.services.device_return import job_guard_deadline
from backend.services.plan_run_context import abort_pending_job_ids
from backend.services.plan_run_offline_policy import (
    is_prolonged_abnormal_offline,
    resolve_run_offline_policy,
)
from backend.services.plan_run_queries import (
    derive_device_link_status,
    device_currently_disconnected,
)
from backend.services.plan_run_read_common import (
    aware,
    iso,
    resolve_step_script,
    snapshot_step_scripts,
)

FAILED_JOB_STATUSES = {JobStatus.FAILED.value}

UNASSIGNED_HOST_KEY = "unassigned"
UNASSIGNED_HOST_LABEL = "未分配"

# 聚合 KPI 并集哨兵（契约 §5.2 规则 3）
STATUS_UNION_ALIASES: Dict[str, frozenset[str]] = {
    "lost_or_backoff": frozenset({"unknown", "backoff"}),
    "unknown_or_backoff": frozenset({"unknown", "backoff"}),
    "stalled": frozenset({"unknown", "backoff"}),
}
LINK_STATUS_ABNORMAL_ALIASES = frozenset({"abnormal", "not_online"})

ALLOWED_DEVICE_SORT_BY = frozenset({
    "device_id",
    "serial",
    "status",
    "link_status",
    "host_id",
    "log_signal_count",
    "patrol_cycle_count",
})
ALLOWED_SORT_DIR = frozenset({"asc", "desc"})


class PlanRunDevicesQueryError(Exception):
    """PlanRun 设备查询参数校验错误（由路由层转为 HTTPException，遵守 C2 分层契约）。"""

    def __init__(self, status_code: int, detail: Any):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class JobFinishingProjection:
    """ADR-0065 D1 单 Job 收尾中只读投影（持久化 status 仍为 RUNNING）。"""

    kind: str
    trigger: str
    deadline_at: Optional[datetime]


def _job_abort_covers(run_context: object, job: Any) -> bool:
    jid = getattr(job, "id", None)
    if jid is None:
        return False
    return int(jid) in abort_pending_job_ids(
        run_context,
        [(int(jid), getattr(job, "host_id", None))],
    )


def _teardown_steps(pipeline_def: object) -> list:
    if not isinstance(pipeline_def, dict):
        return []
    lifecycle = pipeline_def.get("lifecycle") or {}
    if not isinstance(lifecycle, dict):
        return []
    teardown = lifecycle.get("teardown") or []
    return teardown if isinstance(teardown, list) else []


def _init_steps(pipeline_def: object) -> list:
    if not isinstance(pipeline_def, dict):
        return []
    lifecycle = pipeline_def.get("lifecycle") or {}
    if not isinstance(lifecycle, dict):
        return []
    init = lifecycle.get("init") or []
    return init if isinstance(init, list) else []


def _teardown_has_disarm_role_steps(pipeline_def: object) -> bool:
    """Version-aware：``gpu_finish@1.1.0`` 为 collect，不得当成解除义务。"""
    for step in _teardown_steps(pipeline_def):
        if not isinstance(step, dict) or step.get("enabled") is False:
            continue
        name = script_name_from_step(step)
        if not name:
            continue
        role = step_teardown_role(name, step_version_of(step))
        if is_disarm_role(role):
            return True
    return False


def _job_has_teardown_started(job: Any, db: Optional[Session]) -> bool:
    """真实阶段：teardown STARTED 足迹，或单元测试 stub ``lifecycle_stage``。"""
    if getattr(job, "lifecycle_stage", None) == "teardown":
        return True
    if getattr(job, "execution_state", None) == EXECUTING_DISARM:
        return True
    job_id = getattr(job, "id", None)
    if db is None or job_id is None:
        return False
    row = db.execute(
        select(StepTrace.id).where(
            StepTrace.job_id == int(job_id),
            StepTrace.stage == "teardown",
            StepTrace.event_type == "STARTED",
        ).limit(1)
    ).first()
    return row is not None


def _footprint_families_for_job(job: Any, db: Optional[Session]) -> set[str]:
    families: set[str] = set()
    results = getattr(job, "disarm_results", None)
    if isinstance(results, dict):
        families.update(str(k) for k in results.keys())
    job_id = getattr(job, "id", None)
    if db is None or job_id is None:
        return families
    init_by_id: dict[str, dict] = {}
    for step in _init_steps(getattr(job, "pipeline_def", None)):
        if not isinstance(step, dict):
            continue
        key = str(step.get("step_id") or step.get("step_key") or "")
        if key:
            init_by_id[key] = step
    rows = db.execute(
        select(StepTrace.step_id, StepTrace.stage, StepTrace.event_type).where(
            StepTrace.job_id == int(job_id),
        )
    ).all()
    for step_id, stage, event_type in rows:
        if stage != "init" or event_type != "STARTED":
            continue
        step = init_by_id.get(str(step_id or ""))
        if step is None:
            continue
        fam = family_for_footprint_script(script_name_from_step(step))
        if fam:
            families.add(fam)
    return families


def _is_pure_collection_teardown(
    job: Any,
    *,
    db: Optional[Session],
) -> bool:
    """纯采集 teardown：无解除义务，且已真实进入 teardown（B1）。"""
    pipeline = getattr(job, "pipeline_def", None)
    if not _teardown_steps(pipeline):
        return False
    if _teardown_has_disarm_role_steps(pipeline):
        return False
    if not _job_has_teardown_started(job, db):
        return False
    exec_state = getattr(job, "execution_state", None)
    return exec_state in (
        "EXECUTING_STEP",
        "WAITING_EXECUTION_SLOT",
        None,
        EXECUTING_DISARM,
    )


def _is_pending_disarm_finishing(
    job: Any,
    *,
    abort_covers: bool,
    db: Optional[Session],
) -> bool:
    """已有足迹 + abort/exit 覆盖 + 仍有待解除步骤、但 guard 收据尚未到达（B1）。"""
    manual = (getattr(job, "manual_action", None) or "")
    if not (abort_covers or manual == "EXIT_REQUESTED"):
        return False
    footprints = _footprint_families_for_job(job, db)
    if not footprints:
        return False
    pending = select_disarm_steps(
        _teardown_steps(getattr(job, "pipeline_def", None)),
        footprints,
    )
    if not pending:
        return False
    guard = coerce_job_finishing_guard(
        getattr(job, "finishing_guard", None),
        getattr(job, "disarm_guard", None),
    )
    if (
        guard is not None
        and finishing_kind_of(guard) == FINISHING_KIND_TEARDOWN_DISARM
        and not is_clock_regressed(guard)
    ):
        return False
    return True


def _resolve_finishing_trigger(
    job: Any,
    guard: Optional[dict[str, Any]],
    *,
    abort_covers: bool,
) -> str:
    if isinstance(guard, dict):
        trigger = guard.get("trigger")
        if trigger in FINISHING_TRIGGERS:
            return str(trigger)
    if abort_covers:
        return FINISHING_TRIGGER_MANUAL_ABORT
    if (getattr(job, "manual_action", None) or "") == "EXIT_REQUESTED":
        return FINISHING_TRIGGER_MANUAL_EXIT
    return FINISHING_TRIGGER_NATURAL_END


def _atomic_protection_live(
    job: Any,
    guard: Mapping[str, Any] | dict[str, Any] | None,
    *,
    now: datetime,
    db: Optional[Session],
    run_context: object,
    host_status: Optional[str],
    host_last_heartbeat: Optional[datetime],
) -> bool:
    """B 类有效保护：U1 deadline 底座 + 宿主可达/心跳/存活/未 force（B2）。"""
    if not isinstance(guard, dict):
        return False
    if finishing_kind_of(guard) != FINISHING_KIND_ATOMIC_COMMIT:
        return False
    if is_clock_regressed(guard) or guard.get("guard_state") != GUARD_STATE_GRANTED:
        return False
    jid = getattr(job, "id", None)
    if jid is not None and effective_abort_force(
        run_context or {}, int(jid), getattr(job, "host_id", None),
    ):
        return False
    if host_status is not None and host_status != HostStatus.ONLINE.value:
        return False
    hb = aware(host_last_heartbeat)
    if hb is None or (now - hb).total_seconds() >= HOST_HEARTBEAT_TIMEOUT_SECONDS:
        return False
    if db is not None:
        return job_guard_deadline(db, job, now, read_only=True) is not None
    # 无 DB 的单测路径：与 U1 atomic 截止公式同构的只读近似。
    stamp_now = now.timestamp()
    last_server = guard.get("last_server_at", stamp_now)
    if type(last_server) not in (int, float) or stamp_now < float(last_server):
        return False
    liveness = guard.get("last_liveness_receipt_at")
    if type(liveness) not in (int, float):
        return False
    if stamp_now >= float(liveness) + ATOMIC_LIVENESS_FRESH_SECONDS:
        return False
    d_atomic = guard.get("D_atomic")
    if type(d_atomic) not in (int, float) or stamp_now >= float(d_atomic):
        return False
    return True


def project_job_finishing(
    job: Any,
    *,
    now: datetime,
    db: Optional[Session] = None,
    run_context: object = None,
    compute_deadline: bool = True,
    host_status: Optional[str] = None,
    host_last_heartbeat: Optional[datetime] = None,
) -> Optional[JobFinishingProjection]:
    """权威单 Job finishing 判定（Minimap / 分页表 / Job 详情共用，ADR-0065 D1.7）。

    Minimap 只需要是否 finishing（``compute_deadline=False``）时仍须验证有效保护；
    B 类以 U1 ``job_guard_deadline(read_only=True)`` 为底座（#4206 v1.2 / B2）。
    """
    if getattr(job, "status", None) != JobStatus.RUNNING.value:
        return None

    abort_covers = _job_abort_covers(run_context, job) if run_context is not None else False
    guard = coerce_job_finishing_guard(
        getattr(job, "finishing_guard", None),
        getattr(job, "disarm_guard", None),
    )
    kind = finishing_kind_of(guard) if guard is not None else None
    exec_state = getattr(job, "execution_state", None)

    # B 类：有效 grant + 普通 abort 覆盖；force / 失鲜 / 离线不得投影 finishing。
    if kind == FINISHING_KIND_ATOMIC_COMMIT:
        if not abort_covers:
            return None
        if not _atomic_protection_live(
            job,
            guard,
            now=now,
            db=db,
            run_context=run_context,
            host_status=host_status,
            host_last_heartbeat=host_last_heartbeat,
        ):
            return None
        deadline = None
        if compute_deadline and db is not None:
            deadline = job_guard_deadline(db, job, now, read_only=True)
            if deadline is None:
                return None
        return JobFinishingProjection(
            kind=FINISHING_KIND_ATOMIC_COMMIT,
            trigger=_resolve_finishing_trigger(job, guard, abort_covers=abort_covers),
            deadline_at=deadline,
        )

    pure_collection = _is_pure_collection_teardown(job, db=db)
    pending_disarm = _is_pending_disarm_finishing(
        job, abort_covers=abort_covers, db=db,
    )
    teardown_active = False
    if exec_state == EXECUTING_DISARM:
        teardown_active = True
    elif kind == FINISHING_KIND_TEARDOWN_DISARM and guard is not None and not is_clock_regressed(guard):
        teardown_active = True
    elif pure_collection:
        teardown_active = True
    elif pending_disarm:
        teardown_active = True

    if not teardown_active:
        return None

    resolved_kind = FINISHING_KIND_TEARDOWN_DISARM
    deadline: Optional[datetime] = None
    if pure_collection or pending_disarm:
        deadline = None
    elif (
        compute_deadline
        and db is not None
        and guard is not None
        and not is_clock_regressed(guard)
    ):
        deadline = job_guard_deadline(db, job, now, read_only=True)

    return JobFinishingProjection(
        kind=resolved_kind,
        trigger=_resolve_finishing_trigger(
            job, guard if isinstance(guard, dict) else None, abort_covers=abort_covers,
        ),
        deadline_at=deadline,
    )


def critical_section_protects_adb_disconnect(
    job: Any,
    *,
    now: datetime,
    host_last_heartbeat: Optional[datetime],
    db: Optional[Session] = None,
    run_context: object = None,
    host_status: Optional[str] = None,
) -> bool:
    """有效 grant 临界区：U1 保护底座成立时，预期 ADB 断开不得降为 unknown（D1.6 / B2）。"""
    if getattr(job, "status", None) != JobStatus.RUNNING.value:
        return False
    guard = coerce_job_finishing_guard(
        getattr(job, "finishing_guard", None),
        getattr(job, "disarm_guard", None),
    )
    return _atomic_protection_live(
        job,
        guard,
        now=now,
        db=db,
        run_context=run_context,
        host_status=host_status if host_status is not None else HostStatus.ONLINE.value,
        host_last_heartbeat=host_last_heartbeat,
    )


def job_exec_status_for_job(
    j: Any,
    now: datetime,
    *,
    db: Optional[Session] = None,
    run_context: object = None,
    host_status: Optional[str] = None,
    host_last_heartbeat: Optional[datetime] = None,
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
    if project_job_finishing(
        j,
        now=now,
        db=db,
        run_context=run_context,
        compute_deadline=False,
        host_status=host_status,
        host_last_heartbeat=host_last_heartbeat,
    ) is not None:
        return "finishing"
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
    *,
    db: Optional[Session] = None,
    run_context: object = None,
    host_last_heartbeat: Optional[datetime] = None,
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

    finishing = project_job_finishing(
        j,
        now=now,
        db=db,
        run_context=run_context,
        compute_deadline=False,
        host_status=host_status,
        host_last_heartbeat=host_last_heartbeat,
    )
    if finishing is not None:
        return "finishing"

    # RUNNING 分支
    disconnected = device_currently_disconnected(device, host_status)
    if disconnected:
        if critical_section_protects_adb_disconnect(
            j,
            now=now,
            host_last_heartbeat=host_last_heartbeat,
            db=db,
            run_context=run_context,
            host_status=host_status,
        ):
            # 临界区保护：保持 running（未 abort）；不降 unknown。
            pass
        else:
            return "unknown"
    if (j.manual_action or "") == "EXIT_REQUESTED":
        return "backoff"
    nrt = aware(j.next_retry_at)
    if nrt and nrt > now:
        return "backoff"
    return "running"


def aggregate_plan_run_finishing(
    jobs: Sequence[Any],
    *,
    now: datetime,
    db: Optional[Session] = None,
    run_context: object = None,
) -> dict[str, Any]:
    """PlanRun 级 ui_status / 分组计数 / 最近 deadline（ADR-0065 D1.9–D1.12）。"""
    terminal = {
        JobStatus.COMPLETED.value,
        JobStatus.FAILED.value,
        JobStatus.ABORTED.value,
    }
    host_by_id: dict[str, Host] = {}
    if db is not None:
        host_ids = {
            hid for hid in (getattr(j, "host_id", None) for j in jobs) if hid
        }
        if host_ids:
            for host in db.execute(
                select(Host).where(Host.id.in_(host_ids))
            ).scalars().all():
                host_by_id[host.id] = host
    finishing_jobs: list[JobFinishingProjection] = []
    other_active = 0
    for job in jobs:
        status = getattr(job, "status", None)
        if status in terminal:
            continue
        host = host_by_id.get(getattr(job, "host_id", None) or "")
        proj = project_job_finishing(
            job,
            now=now,
            db=db,
            run_context=run_context,
            compute_deadline=True,
            host_status=(
                host.status if host is not None else getattr(job, "host_status", None)
            ),
            host_last_heartbeat=aware(
                host.last_heartbeat
                if host is not None
                else getattr(job, "host_last_heartbeat", None)
            ),
        )
        if proj is not None:
            finishing_jobs.append(proj)
            continue
        other_active += 1

    kind_counts = {
        FINISHING_KIND_TEARDOWN_DISARM: 0,
        FINISHING_KIND_ATOMIC_COMMIT: 0,
    }
    nearest: Optional[datetime] = None
    for proj in finishing_jobs:
        if proj.kind in kind_counts:
            kind_counts[proj.kind] += 1
        if proj.deadline_at is not None:
            nearest = (
                proj.deadline_at
                if nearest is None
                else min(nearest, proj.deadline_at)
            )

    if finishing_jobs and other_active == 0:
        ui_status = "finishing"
    elif any(
        getattr(j, "status", None) == JobStatus.RUNNING.value
        or getattr(j, "status", None) == JobStatus.PENDING.value
        or getattr(j, "status", None) == JobStatus.UNKNOWN.value
        for j in jobs
    ):
        ui_status = "running"
    else:
        ui_status = None

    return {
        "ui_status": ui_status,
        "finishing_kind_counts": kind_counts if finishing_jobs else None,
        "finishing_deadline_at": iso(nearest) if nearest is not None else (
            None if finishing_jobs else None
        ),
        "finishing_job_count": len(finishing_jobs),
        "other_active_count": other_active,
    }


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


def not_reported_liveness_anchor(j: Any) -> Optional[datetime]:
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
    j: Any,
    coord_heartbeat_by_host: Optional[dict[str, datetime]] = None,
    *, db: Optional[Session] = None, now: Optional[datetime] = None,
) -> Optional[datetime]:
    """Projected stuck deadline for a RUNNING job — aligned with
    ``recycler._running_liveness_anchor`` (#993 / ADR-0026 §3).
    """
    if j.status != JobStatus.RUNNING.value:
        return None
    if db is not None and (
        getattr(j, "disarm_guard", None) is not None
        or getattr(j, "finishing_guard", None) is not None
    ):
        fresh = j
        checked_at = max(now or datetime.now(timezone.utc), datetime.now(timezone.utc))
        protected_until = job_guard_deadline(db, fresh, checked_at, read_only=True)
        if protected_until is not None:
            return protected_until
    graded_timeout = running_heartbeat_timeout_seconds(
        j, patrol_stall_multiplier=PATROL_STALL_MULTIPLIER,
    )

    if j.execution_state in ("EXECUTING_STEP", EXECUTING_DISARM):
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


@dataclass(frozen=True)
class _LightweightDeviceStub:
    """供 ``derive_device_link_status`` 复用的轻量设备视图（避免加载整行 Device）。"""

    adb_state: Optional[str]
    adb_connected: Optional[bool]
    status: Optional[str]


@dataclass(frozen=True)
class _LightweightTimeoutJobStub:
    """与 ``JobInstance`` 等价的轻量超时输入，供 ``running_heartbeat_deadline`` 与
    ``job_guard_deadline`` / ``guard_owner_valid`` / ``running_heartbeat_timeout_seconds`` 共用。"""

    id: int
    device_id: int
    host_id: Optional[str]
    status: str
    execution_state: Optional[str]
    last_execution_heartbeat_at: Optional[datetime]
    started_at: Optional[datetime]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    disarm_guard: Optional[Dict[str, Any]]
    finishing_guard: Optional[Dict[str, Any]]
    manual_action: Optional[str]
    pipeline_def: Dict[str, Any]
    patrol_cycle_count: int
    last_patrol_heartbeat_at: Optional[datetime]
    current_patrol_step: Optional[str]
    next_retry_at: Optional[datetime]


def _minimal_patrol_pipeline_def(
    patrol_json_type: Optional[str],
    patrol_interval_raw: Any,
) -> Dict[str, Any]:
    """仅从 SQL JSONB 标量重建 ``running_heartbeat_timeout_seconds`` 所需的 patrol 最小视图。"""
    if patrol_json_type != "object":
        return {}
    patrol: Dict[str, Any] = {}
    if patrol_interval_raw is not None:
        patrol["interval_seconds"] = patrol_interval_raw
    return {"lifecycle": {"patrol": patrol}}


@dataclass(frozen=True)
class _LightPlanRunRow:
    """窄投影中间行：驱动 Minimap 响应与表格过滤/排序/切片，避免先构造重对象。"""

    job_id: int
    device_id: int
    device_serial: Optional[str]
    host_id: Optional[str]
    host_display_name: Optional[str]
    job_exec_status: str
    device_link_status: str
    log_signal_count: int
    patrol_cycle_count: int
    is_stuck: bool
    raw_job_status: str = ""
    device_last_seen: Optional[datetime] = None


def _load_coord_heartbeats(db: Session, run_id: int) -> dict[str, datetime]:
    coord_rows = db.execute(
        select(PlanRunHost.host_id, PlanRunHost.coordinator_heartbeat_at)
        .where(PlanRunHost.plan_run_id == run_id)
    ).all()
    return {
        host_id: aware(hb)
        for host_id, hb in coord_rows
        if hb is not None
    }


def _host_display_name(
    host_id: Optional[str],
    host_name: Optional[str],
    host_ip: Optional[str],
) -> Optional[str]:
    if not host_id:
        return None
    name_clean = (host_name or "").strip()
    if name_clean:
        return name_clean
    ip_clean = (host_ip or "").strip()
    if ip_clean:
        return ip_clean
    return host_id


def _load_lightweight_plan_run_rows(
    db: Session,
    run_id: int,
    *,
    now: datetime,
    include_stuck: bool = True,
) -> Tuple[List[_LightPlanRunRow], Dict[str, int], Dict[str, int], Dict[str, int], Dict[str, str], List[int]]:
    """一次窄列联表扫描生成全集轻量投影、facets、主机显示名映射与全局 ``stuck_job_ids``。"""
    patrol_json = JobInstance.pipeline_def["lifecycle"]["patrol"]
    run_context = db.execute(
        select(PlanRun.run_context).where(PlanRun.id == run_id)
    ).scalar_one_or_none()
    raw_rows = db.execute(
        select(
            JobInstance.id.label("job_id"),
            JobInstance.device_id.label("device_id"),
            JobInstance.host_id.label("host_id"),
            JobInstance.status.label("status"),
            JobInstance.manual_action.label("manual_action"),
            JobInstance.next_retry_at.label("next_retry_at"),
            JobInstance.log_signal_count.label("log_signal_count"),
            JobInstance.patrol_cycle_count.label("patrol_cycle_count"),
            JobInstance.last_patrol_heartbeat_at.label("last_patrol_heartbeat_at"),
            JobInstance.current_patrol_step.label("current_patrol_step"),
            JobInstance.execution_state.label("execution_state"),
            JobInstance.last_execution_heartbeat_at.label("last_execution_heartbeat_at"),
            JobInstance.started_at.label("started_at"),
            JobInstance.created_at.label("created_at"),
            JobInstance.updated_at.label("updated_at"),
            JobInstance.disarm_guard.label("disarm_guard"),
            JobInstance.finishing_guard.label("finishing_guard"),
            JobInstance.pipeline_def.label("pipeline_def"),
            func.jsonb_typeof(patrol_json).label("patrol_json_type"),
            patrol_json["interval_seconds"].label("patrol_interval_raw"),
            Device.id.label("dev_pk"),
            Device.serial.label("device_serial"),
            Device.status.label("device_status"),
            Device.adb_state.label("adb_state"),
            Device.adb_connected.label("adb_connected"),
            Device.last_seen.label("device_last_seen"),
            Host.status.label("host_status"),
            Host.name.label("host_name"),
            Host.ip.label("host_ip"),
            Host.last_heartbeat.label("host_last_heartbeat"),
        )
        .select_from(JobInstance)
        .outerjoin(Device, Device.id == JobInstance.device_id)
        .outerjoin(Host, Host.id == JobInstance.host_id)
        .where(JobInstance.plan_run_id == run_id)
        .order_by(JobInstance.device_id.asc(), JobInstance.id.asc())
    ).all()

    by_status: Dict[str, int] = {"all": 0}
    by_link_status: Dict[str, int] = {"all": 0}
    by_host: Dict[str, int] = {}
    hosts_map: Dict[str, str] = {}
    stuck_job_ids: List[int] = []

    if not raw_rows:
        return [], by_status, by_link_status, by_host, hosts_map, stuck_job_ids

    coord_heartbeat_by_host = _load_coord_heartbeats(db, run_id) if include_stuck else {}
    light_rows: List[_LightPlanRunRow] = []

    for row in raw_rows:
        # Row proxies need ``id`` for abort coverage / finishing projection.
        row_view = SimpleNamespace(
            id=int(row.job_id),
            device_id=int(row.device_id),
            status=row.status,
            manual_action=row.manual_action,
            next_retry_at=row.next_retry_at,
            execution_state=row.execution_state,
            last_execution_heartbeat_at=row.last_execution_heartbeat_at,
            disarm_guard=row.disarm_guard,
            finishing_guard=row.finishing_guard,
            pipeline_def=row.pipeline_def if isinstance(row.pipeline_def, dict) else {},
            host_id=row.host_id,
            host_status=row.host_status,
            host_last_heartbeat=row.host_last_heartbeat,
        )
        dev_stub = (
            _LightweightDeviceStub(
                adb_state=row.adb_state,
                adb_connected=row.adb_connected,
                status=row.device_status,
            )
            if row.dev_pk is not None
            else None
        )
        exec_status = job_exec_status_for_job(
            row_view,
            now,
            db=db,
            run_context=run_context,
            host_status=row.host_status,
            host_last_heartbeat=aware(row.host_last_heartbeat)
            if row.host_last_heartbeat is not None
            else None,
        )
        link_status = derive_device_link_status(dev_stub, row.host_status)
        if include_stuck:
            timeout_stub = _LightweightTimeoutJobStub(
                id=int(row.job_id),
                device_id=int(row.device_id),
                host_id=row.host_id,
                status=row.status,
                execution_state=row.execution_state,
                last_execution_heartbeat_at=row.last_execution_heartbeat_at,
                started_at=row.started_at,
                created_at=row.created_at,
                updated_at=row.updated_at,
                disarm_guard=row.disarm_guard,
                finishing_guard=row.finishing_guard,
                manual_action=row.manual_action,
                patrol_cycle_count=int(row.patrol_cycle_count or 0),
                last_patrol_heartbeat_at=row.last_patrol_heartbeat_at,
                current_patrol_step=row.current_patrol_step,
                next_retry_at=row.next_retry_at,
                pipeline_def=_minimal_patrol_pipeline_def(
                    row.patrol_json_type,
                    row.patrol_interval_raw,
                ),
            )
            hb_deadline = running_heartbeat_deadline(
                timeout_stub, coord_heartbeat_by_host, db=db, now=now,
            )
            stuck = bool(hb_deadline and now >= hb_deadline)
            if stuck:
                stuck_job_ids.append(int(row.job_id))
        else:
            stuck = False

        host_label = _host_display_name(row.host_id, row.host_name, row.host_ip)
        light_rows.append(
            _LightPlanRunRow(
                job_id=int(row.job_id),
                device_id=int(row.device_id),
                device_serial=row.device_serial,
                host_id=row.host_id,
                host_display_name=host_label,
                job_exec_status=exec_status,
                device_link_status=link_status,
                log_signal_count=int(row.log_signal_count or 0),
                patrol_cycle_count=int(row.patrol_cycle_count or 0),
                is_stuck=stuck,
                raw_job_status=str(row.status or ""),
                device_last_seen=row.device_last_seen,
            )
        )

        by_status["all"] += 1
        by_status[exec_status] = by_status.get(exec_status, 0) + 1
        by_link_status["all"] += 1
        by_link_status[link_status] = by_link_status.get(link_status, 0) + 1
        if row.host_id:
            by_host[row.host_id] = by_host.get(row.host_id, 0) + 1
            if row.host_id not in hosts_map:
                hosts_map[row.host_id] = host_label or row.host_id
        else:
            by_host[UNASSIGNED_HOST_KEY] = by_host.get(UNASSIGNED_HOST_KEY, 0) + 1
            hosts_map[UNASSIGNED_HOST_KEY] = UNASSIGNED_HOST_LABEL

    return light_rows, by_status, by_link_status, by_host, hosts_map, stuck_job_ids


def build_plan_run_devices_minimap(
    db: Session,
    run_id: int,
    *,
    now: Optional[datetime] = None,
) -> PlanRunDevicesMinimapOut:
    """构建独立轻量全集 Minimap 响应（严格 7 字段 DTO，绝不先构造重对象）。"""
    ref_now = now or datetime.now(timezone.utc)
    light_rows, by_status, by_link_status, by_host, hosts_map, stuck_job_ids = (
        _load_lightweight_plan_run_rows(db, run_id, now=ref_now)
    )
    run_meta = db.execute(
        select(PlanRun.plan_snapshot, Plan.specialty_key)
        .select_from(PlanRun)
        .outerjoin(Plan, Plan.id == PlanRun.plan_id)
        .where(PlanRun.id == run_id)
    ).first()
    offline_expected, offline_threshold = resolve_run_offline_policy(
        run_meta.plan_snapshot if run_meta else None,
        run_meta.specialty_key if run_meta else None,
    )
    offline_failed_job_ids = [
        row.job_id
        for row in light_rows
        if is_prolonged_abnormal_offline(
            job_status=row.raw_job_status,
            link_status=row.device_link_status,
            device_last_seen=row.device_last_seen,
            now=ref_now,
            offline_expected=offline_expected,
            threshold_seconds=offline_threshold,
        )
    ]
    devices = [
        PlanRunMinimapDeviceOut(
            job_id=row.job_id,
            device_id=row.device_id,
            device_serial=row.device_serial,
            host_id=row.host_id,
            job_exec_status=row.job_exec_status,
            device_link_status=row.device_link_status,
            log_signal_count=row.log_signal_count,
        )
        for row in light_rows
    ]
    return PlanRunDevicesMinimapOut(
        plan_run_id=run_id,
        facets=PlanRunMinimapFacetsOut(
            total=len(light_rows),
            by_status=by_status,
            by_link_status=by_link_status,
            by_host=by_host,
        ),
        hosts=hosts_map,
        stuck_job_ids=stuck_job_ids,
        offline_failed_job_ids=offline_failed_job_ids,
        offline_expected=offline_expected,
        devices=devices,
    )


def _validate_sort(
    sort_by: Optional[str],
    sort_dir: Optional[str],
) -> Tuple[Optional[str], str]:
    norm_by = sort_by.strip().lower() if isinstance(sort_by, str) and sort_by.strip() else None
    if norm_by is not None and norm_by not in ALLOWED_DEVICE_SORT_BY:
        raise PlanRunDevicesQueryError(
            status_code=422,
            detail={
                "code": "INVALID_SORT_BY",
                "message": f"sort_by must be one of {sorted(ALLOWED_DEVICE_SORT_BY)}",
                "allowed": sorted(ALLOWED_DEVICE_SORT_BY),
            },
        )
    norm_dir = sort_dir.strip().lower() if isinstance(sort_dir, str) and sort_dir.strip() else "asc"
    if norm_dir not in ALLOWED_SORT_DIR:
        raise PlanRunDevicesQueryError(
            status_code=422,
            detail={
                "code": "INVALID_SORT_DIR",
                "message": "sort_dir must be 'asc' or 'desc'",
                "allowed": sorted(ALLOWED_SORT_DIR),
            },
        )
    return norm_by, norm_dir


def _matches_status_filter(exec_status: str, status: Optional[str]) -> bool:
    if not status or status.strip().lower() == "all":
        return True
    raw = status.strip().lower()
    if raw in STATUS_UNION_ALIASES:
        return exec_status in STATUS_UNION_ALIASES[raw]
    tokens = {part.strip().lower() for part in raw.split(",") if part.strip()}
    if not tokens or "all" in tokens:
        return True
    expanded: set[str] = set()
    for tok in tokens:
        if tok in STATUS_UNION_ALIASES:
            expanded.update(STATUS_UNION_ALIASES[tok])
        else:
            expanded.add(tok)
    return exec_status in expanded


def _matches_link_status_filter(link_status: str, filter_val: Optional[str]) -> bool:
    if not filter_val or filter_val.strip().lower() == "all":
        return True
    raw = filter_val.strip().lower()
    if raw in LINK_STATUS_ABNORMAL_ALIASES:
        return link_status != "online"
    tokens = {part.strip().lower() for part in raw.split(",") if part.strip()}
    if not tokens or "all" in tokens:
        return True
    if tokens & LINK_STATUS_ABNORMAL_ALIASES:
        return link_status != "online"
    return link_status in tokens


def _matches_host_filter(row_host_id: Optional[str], host_id: Optional[str]) -> bool:
    if not host_id or host_id.strip().lower() == "all":
        return True
    target = host_id.strip()
    if target == UNASSIGNED_HOST_KEY:
        return row_host_id is None
    return row_host_id == target


def _sort_light_rows(
    rows: List[_LightPlanRunRow],
    *,
    sort_by: Optional[str],
    sort_dir: str,
) -> List[_LightPlanRunRow]:
    # 默认序：(device_id ASC, job_id ASC)
    base = sorted(rows, key=lambda r: (r.device_id, r.job_id))
    if sort_by is None or sort_by == "device_id":
        if sort_dir == "desc":
            return sorted(base, key=lambda r: r.device_id, reverse=True)
        return base

    reverse = sort_dir == "desc"
    extractors: Dict[str, Callable[[_LightPlanRunRow], Any]] = {
        "serial": lambda r: (r.device_serial or "").lower(),
        "status": lambda r: r.job_exec_status,
        "link_status": lambda r: r.device_link_status,
        "host_id": lambda r: (r.host_id or "~"),
        "log_signal_count": lambda r: r.log_signal_count,
        "patrol_cycle_count": lambda r: r.patrol_cycle_count,
    }
    return sorted(base, key=extractors[sort_by], reverse=reverse)


def build_device_matrix_item(
    j: JobInstance,
    dev: Optional[Device],
    host_st: Optional[str],
    lease_job_id: Optional[int],
    *,
    coord_heartbeat_by_host: Dict[str, datetime],
    script_index: Dict[Any, Any],
    db: Session,
    now: datetime,
    run_context: object = None,
    host_last_heartbeat: Optional[datetime] = None,
) -> DeviceMatrixItem:
    """构造单台设备的完整 ``DeviceMatrixItem``（供分页当页与单 Job 详情复用）。"""
    serial = dev.serial if dev else None
    model = dev.model if dev else None
    finishing = project_job_finishing(
        j,
        now=now,
        db=db,
        run_context=run_context,
        host_status=host_st,
        host_last_heartbeat=host_last_heartbeat,
    )
    ui = ui_status_for_job(
        j, now, dev, host_st,
        db=db, run_context=run_context, host_last_heartbeat=host_last_heartbeat,
    )
    cur_stage = current_stage_for_job(j)
    pending_deadline = pending_claim_deadline(j)
    heartbeat_deadline = running_heartbeat_deadline(
        j, coord_heartbeat_by_host, db=db, now=now,
    )
    busy_reason, busy_lease_job_id = derive_busy_reason(dev, host_st, lease_job_id)
    link = derive_device_link_status(dev, host_st)
    exec_status = job_exec_status_for_job(
        j,
        now,
        db=db,
        run_context=run_context,
        host_status=host_st,
        host_last_heartbeat=host_last_heartbeat,
    )
    disconnected = device_currently_disconnected(dev, host_st)
    # B5：写侧 POST /manual-retry 对断连一律 409；读侧能力必须同源，不得因
    # finishing / 临界区豁免 ui 降级而误报 manual_retry=true。
    manual_retry_allowed = (
        j.status == JobStatus.RUNNING.value and not disconnected
    )
    cur_step_name, cur_step_version = resolve_step_script(
        script_index, stage="patrol", step_key=j.current_patrol_step,
    )
    return DeviceMatrixItem(
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
        disarm_results=j.disarm_results,
        finishing_kind=finishing.kind if finishing else None,
        finishing_trigger=finishing.trigger if finishing else None,
        finishing_deadline_at=iso(finishing.deadline_at) if finishing else None,
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
        current_script_name=cur_step_name,
        current_script_version=cur_step_version,
    )


def _hydrate_device_matrix_items(
    db: Session,
    run_id: int,
    ordered_job_ids: Sequence[int],
    *,
    plan_snapshot: Optional[dict],
    now: datetime,
) -> List[DeviceMatrixItem]:
    """仅对指定 ``ordered_job_ids``（当页切片）加载重字段并构造 ``DeviceMatrixItem``。"""
    if not ordered_job_ids:
        return []
    script_index = snapshot_step_scripts(plan_snapshot)
    coord_heartbeat_by_host = _load_coord_heartbeats(db, run_id)
    run_context = db.execute(
        select(PlanRun.run_context).where(PlanRun.id == run_id)
    ).scalar_one_or_none()
    joined = db.execute(
        select(
            JobInstance,
            Device,
            Host.status.label("host_status"),
            Host.last_heartbeat.label("host_last_heartbeat"),
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
        .where(
            JobInstance.plan_run_id == run_id,
            JobInstance.id.in_(ordered_job_ids),
        )
    ).all()
    by_job_id: Dict[int, DeviceMatrixItem] = {}
    for j, dev, host_st, host_hb, lease_job_id in joined:
        by_job_id[int(j.id)] = build_device_matrix_item(
            j,
            dev,
            host_st,
            lease_job_id,
            coord_heartbeat_by_host=coord_heartbeat_by_host,
            script_index=script_index,
            db=db,
            now=now,
            run_context=run_context,
            host_last_heartbeat=aware(host_hb) if host_hb is not None else None,
        )
    return [by_job_id[jid] for jid in ordered_job_ids if jid in by_job_id]


def build_plan_run_devices(
    db: Session,
    run_id: int,
    *,
    status: Optional[str] = None,
    link_status: Optional[str] = None,
    host_id: Optional[str] = None,
    q: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_dir: Optional[str] = None,
    skip: Optional[int] = None,
    limit: Optional[int] = None,
    plan_snapshot: Optional[dict] = None,
) -> PlanRunDevicesOut:
    norm_sort_by, norm_sort_dir = _validate_sort(sort_by, sort_dir)
    now = datetime.now(timezone.utc)
    light_rows, by_status, by_link_status, by_host, _hosts_map, _stuck = (
        _load_lightweight_plan_run_rows(db, run_id, now=now, include_stuck=False)
    )
    if not light_rows:
        return PlanRunDevicesOut(
            plan_run_id=run_id,
            total=0,
            filtered_total=0,
            by_status={"all": 0},
            by_link_status={"all": 0},
            by_host={},
            devices=[],
        )

    # 保持历史 by_host 兼容性：无主机行不破坏既有 by_host 键（仅保留非空 host_id 计数）
    legacy_by_host = {k: v for k, v in by_host.items() if k != UNASSIGNED_HOST_KEY}

    q_lower = q.strip().lower() if isinstance(q, str) and q.strip() else None
    filtered_rows = [
        row
        for row in light_rows
        if _matches_status_filter(row.job_exec_status, status)
        and _matches_link_status_filter(row.device_link_status, link_status)
        and _matches_host_filter(row.host_id, host_id)
        and (q_lower is None or q_lower in (row.device_serial or "").lower())
    ]
    filtered_total = len(filtered_rows)
    ordered_rows = _sort_light_rows(
        filtered_rows,
        sort_by=norm_sort_by,
        sort_dir=norm_sort_dir,
    )

    # 真分页切片：仅传 skip 或 limit 时切片；未传时保持全量向后兼容
    if skip is not None or limit is not None:
        eff_skip = max(0, skip or 0)
        eff_limit = max(1, limit or 50)
        page_rows = ordered_rows[eff_skip : eff_skip + eff_limit]
    else:
        page_rows = ordered_rows

    # 仅对当前页的 job_id 构造重 DeviceMatrixItem
    page_items = _hydrate_device_matrix_items(
        db,
        run_id,
        [row.job_id for row in page_rows],
        plan_snapshot=plan_snapshot,
        now=now,
    )
    return PlanRunDevicesOut(
        plan_run_id=run_id,
        total=len(light_rows),
        filtered_total=filtered_total,
        by_status=by_status,
        by_link_status=by_link_status,
        by_host=legacy_by_host,
        devices=page_items,
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
