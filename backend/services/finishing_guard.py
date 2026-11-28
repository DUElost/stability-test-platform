"""Control-plane FinishingGuard atomic_commit handshake (ADR-0065 / B12 U4 / #4206).

Lock orders (deadlock-safe, ADR-0065 D4):
  pre_register:     Job FOR UPDATE → PlanRun FOR NO KEY UPDATE → admission FOR SHARE
  activate_permit:  Host FOR SHARE → Job FOR UPDATE → PlanRun FOR NO KEY UPDATE
  close admission:  admission FOR UPDATE (admission_open=false)
"""

from __future__ import annotations

import logging
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agent.contracts.capabilities import (
    ATOMIC_COMMIT,
    FINISHING_GUARD_V2,
    FORCE_ABORT,
    normalize_capabilities,
)
from backend.agent.contracts.finishing_guard import (
    FINISHING_KIND_ATOMIC_COMMIT,
    GUARD_INVALIDATED_CLOCK_REGRESSED,
    GUARD_STATE_GRANTED,
    GUARD_STATE_PRE_REGISTERED,
    PRE_REG_GRACE_SECONDS,
    ATOMIC_HARD_CAP_SECONDS,
    ATOMIC_SILENT_WRITE_PROGRESS_WINDOW_SECONDS,
    coerce_job_finishing_guard,
    is_clock_regressed,
    normalize_atomic_step_timeout,
)
from backend.core.job_timeout_config import HOST_HEARTBEAT_TIMEOUT_SECONDS
from backend.models.device_lease import DeviceLease
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType
from backend.models.finishing_guard_admission import (
    ADMISSION_ROW_ID,
    FinishingGuardAdmission,
)
from backend.models.host import Host
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun
from backend.services.device_return import (
    _upsert_open_debt_sync,
    close_flash_integrity_debts_sync,
)
from backend.services.plan_run_context import (
    abort_pending_job_ids,
    effective_abort_force,
)

logger = logging.getLogger(__name__)

FLASH_INTEGRITY_FAMILY = "flash_integrity"
DEBT_SOURCE_JOB = "job"


class FinishingGuardError(Exception):
    def __init__(self, code: str, message: str = "", **extra: Any) -> None:
        super().__init__(message or code)
        self.code = code
        self.extra = extra


# ── capability negotiation ───────────────────────────────────────────────────


def control_plane_finishing_capabilities() -> frozenset[str]:
    return frozenset({FINISHING_GUARD_V2, ATOMIC_COMMIT, FORCE_ABORT})


def negotiate_job_capabilities(
    host_capabilities: object,
    *,
    script_requires: Mapping[str, object] | None = None,
) -> dict[str, bool]:
    """Intersect host heartbeat, script manifest, and control-plane support."""
    host_caps = set(normalize_capabilities(host_capabilities))
    plane = control_plane_finishing_capabilities()
    negotiated = {cap: cap in host_caps and cap in plane for cap in plane}
    if script_requires:
        req = script_requires.get("requires_agent_capabilities")
        if isinstance(req, list):
            for cap in normalize_capabilities(req):
                negotiated[cap] = negotiated.get(cap, False) and cap in host_caps
    return negotiated


def host_supports_force_abort(host_capabilities: object) -> bool:
    return FORCE_ABORT in normalize_capabilities(host_capabilities)


# ── lock chains ──────────────────────────────────────────────────────────────


def _lock_admission_share(db: Session) -> FinishingGuardAdmission:
    row = db.execute(
        select(FinishingGuardAdmission)
        .where(FinishingGuardAdmission.id == ADMISSION_ROW_ID)
        .with_for_update(read=True)  # FOR SHARE
    ).scalar_one_or_none()
    if row is None:
        raise FinishingGuardError("admission_row_missing")
    return row


def _lock_pre_register_chain(
    db: Session, *, job_id: int, plan_run_id: int,
) -> tuple[JobInstance, PlanRun, FinishingGuardAdmission]:
    job = db.execute(
        select(JobInstance)
        .where(JobInstance.id == job_id)
        .with_for_update()
    ).scalar_one_or_none()
    if job is None:
        raise FinishingGuardError("job_not_found")
    plan_run = db.execute(
        select(PlanRun)
        .where(PlanRun.id == plan_run_id)
        .with_for_update(key_share=True)
    ).scalar_one_or_none()
    if plan_run is None:
        raise FinishingGuardError("plan_run_not_found")
    admission = _lock_admission_share(db)
    return job, plan_run, admission


def _lock_activate_permit_chain(
    db: Session, *, job_id: int, plan_run_id: int, host_id: str,
) -> tuple[Host, JobInstance, PlanRun]:
    host = db.execute(
        select(Host)
        .where(Host.id == host_id)
        .with_for_update(read=True)
    ).scalar_one_or_none()
    if host is None:
        raise FinishingGuardError("host_not_found")
    job = db.execute(
        select(JobInstance)
        .where(JobInstance.id == job_id)
        .with_for_update()
    ).scalar_one_or_none()
    if job is None:
        raise FinishingGuardError("job_not_found")
    plan_run = db.execute(
        select(PlanRun)
        .where(PlanRun.id == plan_run_id)
        .with_for_update(key_share=True)
    ).scalar_one_or_none()
    if plan_run is None:
        raise FinishingGuardError("plan_run_not_found")
    return host, job, plan_run


def _lock_admission_exclusive(db: Session) -> FinishingGuardAdmission:
    return db.execute(
        select(FinishingGuardAdmission)
        .where(FinishingGuardAdmission.id == ADMISSION_ROW_ID)
        .with_for_update()
    ).scalar_one()


# ── admission admin ──────────────────────────────────────────────────────────


def close_admission_sync(db: Session) -> dict[str, Any]:
    row = _lock_admission_exclusive(db)
    row.admission_open = False
    db.flush()
    return {"admission_open": False, "active_guards": count_active_atomic_guards_sync(db)}


def count_active_atomic_guards_sync(db: Session) -> int:
    """Count RUNNING jobs with atomic_commit guard in pre_registered or granted."""
    rows = db.execute(
        select(JobInstance.id, JobInstance.finishing_guard).where(
            JobInstance.status == JobStatus.RUNNING.value,
            JobInstance.finishing_guard.isnot(None),
        )
    ).all()
    count = 0
    for _jid, guard in rows:
        if not isinstance(guard, dict):
            continue
        if guard.get("kind") != FINISHING_KIND_ATOMIC_COMMIT:
            continue
        state = guard.get("guard_state")
        if state in (GUARD_STATE_PRE_REGISTERED, GUARD_STATE_GRANTED):
            if not is_clock_regressed(guard):
                count += 1
    return count


def is_admission_open_sync(db: Session) -> bool:
    row = db.get(FinishingGuardAdmission, ADMISSION_ROW_ID)
    return bool(row and row.admission_open)


# ── helpers ──────────────────────────────────────────────────────────────────


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _epoch(now: datetime | None = None) -> float:
    t = now or _utc_now()
    return t.timestamp()


def _abort_committed_for_job(plan_run: PlanRun, job: JobInstance) -> bool:
    ctx = plan_run.run_context if isinstance(plan_run.run_context, dict) else {}
    pending = abort_pending_job_ids(ctx, [(job.id, job.host_id)])
    return job.id in pending


def _host_online_fresh(host: Host, *, now: datetime, boot_id: str | None) -> bool:
    if host.status != HostStatus.ONLINE.value:
        return False
    if host.retired_at is not None:
        return False
    if boot_id and host.boot_id and host.boot_id != boot_id:
        return False
    hb = host.last_heartbeat
    if hb is None:
        return False
    if hb.tzinfo is None:
        hb = hb.replace(tzinfo=timezone.utc)
    return (now - hb).total_seconds() <= HOST_HEARTBEAT_TIMEOUT_SECONDS


def _active_job_lease(
    db: Session, *, device_id: int, job_id: int, now: datetime,
) -> DeviceLease | None:
    lease = db.execute(
        select(DeviceLease).where(
            DeviceLease.device_id == device_id,
            DeviceLease.job_id == job_id,
            DeviceLease.lease_type == LeaseType.JOB.value,
            DeviceLease.status == LeaseStatus.ACTIVE.value,
        )
    ).scalars().first()
    if lease is None:
        return None
    exp = lease.expires_at
    if exp is None:
        return None
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp <= now:
        return None
    return lease


# Platform reserve folded into PLAN_INIT_WALL_CAP (ADR-0065 D4.9); kept small —
# real retry budgets arrive via second-layer frozen member timeouts.
_PLAN_INIT_PLATFORM_RESERVE_SECONDS = 60


def _snapshot_steps(plan_snapshot: object) -> list[dict[str, Any]]:
    if not isinstance(plan_snapshot, dict):
        return []
    steps = plan_snapshot.get("steps")
    if not isinstance(steps, list):
        return []
    return [s for s in steps if isinstance(s, dict)]


def _step_identity(step: Mapping[str, Any]) -> str:
    for key in ("step_key", "script_name", "id"):
        value = step.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _normalize_frozen_members(
    raw: list[Any],
    *,
    snapshot_steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve atomic_group_frozen to ordered members with normalized timeouts.

    Entries may be step identity strings (looked up in plan_snapshot) or dicts
    carrying explicit ``timeout_seconds`` (VBUS return_check second-layer freeze).
    """
    by_id = {_step_identity(s): s for s in snapshot_steps if _step_identity(s)}
    # Static first-layer: snapshot steps that declare the same atomic_group.
    snapshot_atomic = [
        s for s in snapshot_steps
        if s.get("atomic_group") and s.get("enabled") is not False
    ]
    members: list[dict[str, Any]] = []
    seen: set[str] = set()

    source = list(raw) if raw else [
        _step_identity(s) for s in sorted(
            snapshot_atomic,
            key=lambda s: (int(s.get("sort_order") or 0), _step_identity(s)),
        )
    ]
    for item in source:
        if isinstance(item, str):
            step = by_id.get(item.strip())
            if step is None:
                # Unknown identity still counts with default timeout (fail-closed budget).
                ident = item.strip()
                timeout = normalize_atomic_step_timeout(None)
            else:
                ident = _step_identity(step) or item.strip()
                timeout = normalize_atomic_step_timeout(step.get("timeout_seconds"))
            payload = {
                "step_key": ident,
                "timeout_seconds": timeout,
                "source": "snapshot" if step is not None else "client",
            }
        elif isinstance(item, Mapping):
            ident = str(
                item.get("step_key")
                or item.get("script_name")
                or item.get("id")
                or ""
            ).strip()
            timeout = normalize_atomic_step_timeout(item.get("timeout_seconds"))
            payload = {
                "step_key": ident or f"frozen_{len(members)}",
                "timeout_seconds": timeout,
                "source": str(item.get("source") or "client"),
            }
        else:
            continue
        if payload["step_key"] in seen:
            continue
        seen.add(payload["step_key"])
        members.append(payload)
    return members


def _plan_init_wall_cap_seconds(
    snapshot_steps: list[dict[str, Any]],
    frozen_members: list[Mapping[str, Any]],
) -> int:
    """PLAN_INIT_WALL_CAP：init 归一化超时之和 + 二层冻结成员 + 平台余量。

    不得引用 ``lifecycle.timeout_seconds``（ADR-0065 D4.9）。
    """
    total = 0
    for step in snapshot_steps:
        if step.get("enabled") is False:
            continue
        stage = str(step.get("stage") or "").lower()
        if stage and stage != "init":
            continue
        total += normalize_atomic_step_timeout(step.get("timeout_seconds"))
    # Second-layer members not already covered by init steps (e.g. synthetic
    # VBUS return_check) still extend the wall cap.
    covered = {
        _step_identity(s)
        for s in snapshot_steps
        if _step_identity(s)
    }
    for member in frozen_members:
        key = str(member.get("step_key") or "")
        if key and key not in covered:
            total += normalize_atomic_step_timeout(member.get("timeout_seconds"))
    return total + _PLAN_INIT_PLATFORM_RESERVE_SECONDS


def compute_d_atomic(
    *,
    t_arm: float,
    e_curr_rem: float,
    subsequent_timeouts: list[int],
    t_job_start: float | None,
    plan_init_wall_cap: int,
) -> float:
    """``D_atomic = min(D_steps, D_cap, D_run)`` (ADR-0065 D4.9)."""
    d_steps = t_arm + max(0.0, float(e_curr_rem)) + float(sum(subsequent_timeouts))
    d_cap = t_arm + float(ATOMIC_HARD_CAP_SECONDS)
    if t_job_start is None:
        d_run = d_cap
    else:
        d_run = float(t_job_start) + float(max(plan_init_wall_cap, 0))
    return min(d_steps, d_cap, d_run)


# ── payloads ─────────────────────────────────────────────────────────────────


class PreRegisterIn(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    step_timeout_seconds: int | None = None
    min_critical_section_seconds: int = Field(default=0, ge=0)
    #: Ordered frozen members: step identity strings and/or dicts with
    #: ``timeout_seconds`` (VBUS return_check second-layer expansions).
    atomic_group_frozen: list[Any] = Field(default_factory=list)
    current_step_key: str | None = None
    elapsed_pre_arm_seconds: float = Field(default=0.0, ge=0.0)
    t_arm: float | None = None
    client_boot_id: str = ""


class ActivatePermitIn(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    client_boot_id: str = ""


class CancelPreRegistrationIn(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)


class GuardHeartbeatIn(BaseModel):
    request_id: str = ""
    liveness: bool = True
    progress_seq: int | None = None
    client_time: float | None = None


# ── pre_register ─────────────────────────────────────────────────────────────


def pre_register_atomic_commit_sync(
    db: Session,
    *,
    job_id: int,
    payload: PreRegisterIn,
) -> dict[str, Any]:
    now = _utc_now()
    t_arm = float(payload.t_arm) if payload.t_arm is not None else _epoch(now)

    probe = db.get(JobInstance, job_id)
    if probe is None or probe.plan_run_id is None:
        raise FinishingGuardError("job_not_found")
    job, plan_run, admission = _lock_pre_register_chain(
        db, job_id=job_id, plan_run_id=probe.plan_run_id,
    )

    if not admission.admission_open:
        raise FinishingGuardError("admission_closed")

    if job.status != JobStatus.RUNNING.value:
        raise FinishingGuardError("job_not_running", job_status=job.status)

    negotiated = job.negotiated_capabilities if isinstance(job.negotiated_capabilities, dict) else {}
    if not negotiated.get(FINISHING_GUARD_V2) or not negotiated.get(ATOMIC_COMMIT):
        raise FinishingGuardError("atomic_commit_not_negotiated")

    existing = coerce_job_finishing_guard(job.finishing_guard, job.disarm_guard)
    if existing and existing.get("kind") == FINISHING_KIND_ATOMIC_COMMIT:
        if existing.get("request_id") == payload.request_id:
            return {"status": "ok", "idempotent": True, "finishing_guard": existing}
        if existing.get("guard_state") == GUARD_STATE_GRANTED:
            raise FinishingGuardError("already_granted")
        if existing.get("guard_state") == GUARD_STATE_PRE_REGISTERED:
            raise FinishingGuardError("request_id_mismatch")

    if _abort_committed_for_job(plan_run, job):
        raise FinishingGuardError("abort_already_committed")

    if effective_abort_force(plan_run.run_context, job.id, job.host_id):
        raise FinishingGuardError("force_abort_pending")

    snapshot_steps = _snapshot_steps(plan_run.plan_snapshot)
    frozen_members = _normalize_frozen_members(
        list(payload.atomic_group_frozen or []),
        snapshot_steps=snapshot_steps,
    )
    if not frozen_members:
        # Fail closed: without a resolvable atomic_group the control plane
        # cannot freeze D_steps / D_run honestly.
        raise FinishingGuardError("atomic_group_frozen_empty")

    current_key = (payload.current_step_key or "").strip()
    if current_key:
        curr_idx = next(
            (i for i, m in enumerate(frozen_members) if m["step_key"] == current_key),
            0,
        )
    else:
        curr_idx = 0
    current_member = frozen_members[curr_idx]
    e_curr_norm = normalize_atomic_step_timeout(
        payload.step_timeout_seconds
        if payload.step_timeout_seconds is not None
        else current_member.get("timeout_seconds")
    )
    e_curr_rem = max(0.0, float(e_curr_norm) - float(payload.elapsed_pre_arm_seconds or 0.0))
    subsequent = [
        int(m["timeout_seconds"])
        for m in frozen_members[curr_idx + 1 :]
    ]
    t_job_start: float | None = None
    if isinstance(job.started_at, datetime):
        started = job.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        t_job_start = started.timestamp()
    plan_init_wall = _plan_init_wall_cap_seconds(snapshot_steps, frozen_members)
    d_atomic = compute_d_atomic(
        t_arm=t_arm,
        e_curr_rem=e_curr_rem,
        subsequent_timeouts=subsequent,
        t_job_start=t_job_start,
        plan_init_wall_cap=plan_init_wall,
    )
    min_cs = payload.min_critical_section_seconds
    if e_curr_rem < min_cs or (d_atomic - t_arm) < min_cs:
        raise FinishingGuardError("insufficient_atomic_budget")

    permit_expires_at = min(d_atomic, t_arm + PRE_REG_GRACE_SECONDS)

    host = db.get(Host, job.host_id) if job.host_id else None
    lease = _active_job_lease(db, device_id=job.device_id, job_id=job.id, now=now)
    fencing_token = lease.fencing_token if lease else None

    guard: dict[str, Any] = {
        "kind": FINISHING_KIND_ATOMIC_COMMIT,
        "guard_state": GUARD_STATE_PRE_REGISTERED,
        "request_id": payload.request_id,
        "permit_id": payload.request_id,
        "seq": int(time.time() * 1000),
        "t0": t_arm,
        "T_arm": t_arm,
        "D_atomic": d_atomic,
        "D_steps": t_arm + e_curr_rem + float(sum(subsequent)),
        "D_cap": t_arm + float(ATOMIC_HARD_CAP_SECONDS),
        "D_run": (
            float(t_job_start) + float(plan_init_wall)
            if t_job_start is not None
            else t_arm + float(ATOMIC_HARD_CAP_SECONDS)
        ),
        "E_curr_rem": e_curr_rem,
        "permit_expires_at": permit_expires_at,
        "last_liveness_receipt_at": t_arm,
        "last_progress_receipt_at": t_arm,
        "last_server_at": _epoch(now),
        "silent_write_progress_window_seconds": ATOMIC_SILENT_WRITE_PROGRESS_WINDOW_SECONDS,
        "atomic_group_frozen": frozen_members,
        "negotiated_capabilities": dict(negotiated),
    }
    job.finishing_guard = guard

    if host is not None and lease is not None:
        _upsert_open_debt_sync(
            db,
            device_id=job.device_id,
            family=FLASH_INTEGRITY_FAMILY,
            source_kind=DEBT_SOURCE_JOB,
            source_job_id=job.id,
            source_fencing_token=fencing_token,
            cause="atomic_pre_register",
            evidence={"request_id": payload.request_id, "t_arm": t_arm},
            now=now,
        )

    db.flush()
    return {
        "status": "pre_registered",
        "D_atomic": d_atomic,
        "permit_expires_at": permit_expires_at,
        "finishing_guard": guard,
    }


# ── activate_permit ───────────────────────────────────────────────────────────


def activate_permit_sync(
    db: Session,
    *,
    job_id: int,
    host_id: str,
    payload: ActivatePermitIn,
) -> dict[str, Any]:
    now = _utc_now()
    job = db.get(JobInstance, job_id)
    if job is None or job.plan_run_id is None:
        raise FinishingGuardError("job_not_found")

    host, job, plan_run = _lock_activate_permit_chain(
        db,
        job_id=job_id,
        plan_run_id=job.plan_run_id,
        host_id=host_id,
    )

    if job.host_id != host_id:
        raise FinishingGuardError("host_job_mismatch")

    guard = coerce_job_finishing_guard(job.finishing_guard, job.disarm_guard)
    if guard is None or guard.get("kind") != FINISHING_KIND_ATOMIC_COMMIT:
        raise FinishingGuardError("no_atomic_guard")

    if guard.get("request_id") != payload.request_id:
        raise FinishingGuardError("request_id_mismatch")

    if is_clock_regressed(guard):
        raise FinishingGuardError("guard_clock_regressed")

    if guard.get("guard_state") == GUARD_STATE_GRANTED:
        return {
            "status": "granted",
            "idempotent": True,
            "D_atomic": guard.get("D_atomic"),
            "finishing_guard": guard,
        }

    if guard.get("guard_state") != GUARD_STATE_PRE_REGISTERED:
        raise FinishingGuardError("invalid_guard_state")

    if not _host_online_fresh(host, now=now, boot_id=payload.client_boot_id or None):
        raise FinishingGuardError("host_not_online_fresh")

    lease = _active_job_lease(db, device_id=job.device_id, job_id=job.id, now=now)
    if lease is None:
        raise FinishingGuardError("no_active_lease")

    if _abort_committed_for_job(plan_run, job):
        raise FinishingGuardError("abort_already_committed")

    if effective_abort_force(plan_run.run_context, job.id, host_id):
        raise FinishingGuardError("force_abort_pending")

    permit_exp = guard.get("permit_expires_at")
    if type(permit_exp) in (int, float) and _epoch(now) > float(permit_exp):
        raise FinishingGuardError("pre_register_expired")

    updated = deepcopy(guard)
    updated["guard_state"] = GUARD_STATE_GRANTED
    updated["granted_at"] = _epoch(now)
    updated["last_liveness_receipt_at"] = _epoch(now)
    updated["last_progress_receipt_at"] = _epoch(now)
    updated["last_server_at"] = _epoch(now)
    job.finishing_guard = updated
    db.flush()

    lease_exp = lease.expires_at
    if lease_exp is not None and lease_exp.tzinfo is None:
        lease_exp = lease_exp.replace(tzinfo=timezone.utc)
    lease_remaining = (
        max(0, int((lease_exp - now).total_seconds())) if lease_exp is not None else 0
    )
    hb = host.last_heartbeat
    if hb is not None and hb.tzinfo is None:
        hb = hb.replace(tzinfo=timezone.utc)
    host_hb_remaining = 0
    if hb is not None:
        host_hb_remaining = max(
            0,
            int(HOST_HEARTBEAT_TIMEOUT_SECONDS - (now - hb).total_seconds()),
        )
    return {
        "status": "granted",
        "activated_at": updated.get("granted_at"),
        "D_atomic": updated.get("D_atomic"),
        "lease_expires_at": lease_exp.isoformat() if lease_exp is not None else None,
        "lease_remaining_seconds": lease_remaining,
        "host_heartbeat_remaining_seconds": host_hb_remaining,
        "finishing_guard": updated,
    }


# ── cancel pre-registration ───────────────────────────────────────────────────


def cancel_pre_registration_sync(
    db: Session,
    *,
    job_id: int,
    payload: CancelPreRegistrationIn,
) -> dict[str, Any]:
    job = db.execute(
        select(JobInstance).where(JobInstance.id == job_id).with_for_update()
    ).scalar_one_or_none()
    if job is None:
        raise FinishingGuardError("job_not_found")

    guard = coerce_job_finishing_guard(job.finishing_guard, job.disarm_guard)
    if guard is None:
        return {"status": "noop", "reason": "no_guard"}

    if guard.get("kind") != FINISHING_KIND_ATOMIC_COMMIT:
        raise FinishingGuardError("not_atomic_commit")

    if guard.get("guard_state") == GUARD_STATE_GRANTED:
        raise FinishingGuardError("already_granted")

    if guard.get("request_id") != payload.request_id:
        raise FinishingGuardError("request_id_mismatch")

    # Never granted ⇒ revoke flash_integrity obligation opened at pre_register.
    close_flash_integrity_debts_sync(
        db,
        device_id=job.device_id,
        closed_reason="cancelled_pre_registration",
        now=_utc_now(),
    )
    job.finishing_guard = None
    db.flush()
    return {"status": "cancelled"}


# ── liveness / progress ───────────────────────────────────────────────────────


def record_guard_heartbeat_sync(
    db: Session,
    *,
    job_id: int,
    payload: GuardHeartbeatIn,
) -> dict[str, Any]:
    now = _utc_now()
    server_now = _epoch(now)

    job = db.execute(
        select(JobInstance).where(JobInstance.id == job_id).with_for_update()
    ).scalar_one_or_none()
    if job is None:
        raise FinishingGuardError("job_not_found")

    guard = coerce_job_finishing_guard(job.finishing_guard, job.disarm_guard)
    if guard is None or guard.get("kind") != FINISHING_KIND_ATOMIC_COMMIT:
        raise FinishingGuardError("no_atomic_guard")

    if payload.request_id and guard.get("request_id") != payload.request_id:
        raise FinishingGuardError("request_id_mismatch")

    if payload.client_time is not None:
        client_t = float(payload.client_time)
        last_server = float(guard.get("last_server_at", server_now) or server_now)
        if client_t + 1.0 < last_server:
            updated = mark_clock_regressed_guard(guard)
            job.finishing_guard = updated
            db.flush()
            return {"status": "clock_regressed", "finishing_guard": updated}

    updated = deepcopy(guard)
    if payload.liveness:
        updated["last_liveness_receipt_at"] = server_now
    if payload.progress_seq is not None:
        prev = updated.get("progress_seq")
        if type(prev) is int and payload.progress_seq > prev:
            updated["progress_seq"] = payload.progress_seq
            updated["last_progress_receipt_at"] = server_now
        elif prev is None:
            updated["progress_seq"] = payload.progress_seq
            updated["last_progress_receipt_at"] = server_now
    updated["last_server_at"] = server_now
    job.finishing_guard = updated
    db.flush()
    return {"status": "ok", "finishing_guard": updated}


def mark_clock_regressed_guard(guard: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(guard)
    out["reason"] = GUARD_INVALIDATED_CLOCK_REGRESSED
    out["guard_invalidated_reason"] = GUARD_INVALIDATED_CLOCK_REGRESSED
    return out


# ── sync entry for abort interleaving (job lock only) ─────────────────────────


def try_pre_register_with_job_lock(
    db: Session,
    *,
    job_id: int,
    hold_event: Any = None,
) -> dict[str, Any]:
    """Test hook: hold job row lock until release."""
    job = db.execute(
        select(JobInstance).where(JobInstance.id == job_id).with_for_update()
    ).scalar_one()
    if hold_event is not None:
        hold_event.set()
        hold_event.wait(timeout=30)
    return {"held_job_id": job.id}