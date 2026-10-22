"""Device-return debt control plane — ADR-0061 D5 / #3723 §3.4 U3.

Unified entry for every JOB-lease release path listed in #3723 §1.2.
Within one device row lock the service:

1. Maps job ``disarm_results`` into per-family return_state + open debt rows;
2. Converts the JOB lease into a MAINTENANCE debt lease (or releases it) so
   the device always has exactly one ACTIVE lease while open debts exist;
3. Advances ``lease_generation`` on conversion so the old fencing token dies.

Debt leases do **not** auto-expire: protection is ``lease_type=MAINTENANCE`` +
``reason=归还欠账`` (reconciler / emergency release already refuse non-JOB).
``expires_at`` is a schema filler only — never the release decision.
``host_id`` is the device's real host, never a fictional platform host.
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from sqlalchemy.orm.attributes import flag_modified
from backend.models.host import Host
from backend.models.plan_run import PlanRun
from backend.models.audit import AuditLog
from backend.services.audit_writer import record_audit, record_audit_async
from backend.core.job_timeout_config import HOST_HEARTBEAT_TIMEOUT_SECONDS, running_heartbeat_timeout_seconds, abort_reap_clock
from backend.agent.contracts.device_return import (
    disarm_guard_deadline, freeze_disarm_guard, receive_disarm_evidence, select_disarm_steps,
    probe_result_policy, step_version_of, PLATFORM_INTERRUPTED_EXIT_CODES,
)

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.agent.contracts.device_return import (
    DEBT_CLOSE_DISCHARGED,
    DEBT_CLOSE_OVERRIDDEN,
    DEBT_STATE_CLOSED,
    DEBT_STATE_OPEN,
    DISARM_RESULT_CONFIRMED,
    DISARM_RESULT_EXECUTED_UNVERIFIED,
    DISARM_RESULT_FAILED,
    DISARM_RESULT_INCOMPLETE,
    DISARM_RESULT_NOT_EXECUTED,
    DISARM_RESULT_READBACK_FAILED,
    FAMILY_RETURN_DEBT,
    FAMILY_RETURN_RETURNED,
    FAMILY_RETURN_UNVERIFIED,
    FAMILY_STEPS,
    ONBOARDED,
    RETURN_SOURCE_DISCHARGE,
    RETURN_SOURCE_JOB,
    RETURN_SOURCE_OVERRIDE,
    RETURN_SOURCE_STOCK,
    family_for_footprint_script,
    probe_entry_response,
    script_name_from_step,
)
from backend.core.metrics import (
    set_device_return_debt_oldest_seconds,
    set_device_return_debt_open,
)
from backend.models.device_lease import DeviceLease
from backend.models.device_return_debt import DeviceReturnDebt
from backend.models.enums import LeaseStatus, LeaseType
from backend.models.host import Device
from backend.models.job import JobInstance, StepTrace
from backend.services.disarm_derive import derive_disarm_results_from_traces

logger = logging.getLogger(__name__)

# D5.2: MAINTENANCE debt lease markers (machine-stable).
DEBT_LEASE_REASON = "归还欠账"
DEBT_LEASE_HOLDER = "platform"
DEBT_LEASE_AGENT_INSTANCE_ID = "platform"
# Schema filler only — release decisions never read this for debt leases.
_DEBT_LEASE_EXPIRES_AT = datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

_SOURCE_KIND_JOB = "job"
_SOURCE_KIND_STOCK = "stock"

_DEBT_CAUSES = frozenset({
    DISARM_RESULT_FAILED,
    DISARM_RESULT_READBACK_FAILED,
    DISARM_RESULT_NOT_EXECUTED,
    DISARM_RESULT_INCOMPLETE,
})


@dataclass(frozen=True)
class LeaseOutcome:
    """Result of handling a JOB lease at a terminal / release point."""

    released: bool
    converted_to_debt: bool
    open_debt_count: int
    fencing_token: Optional[str] = None


def is_debt_lease(lease: DeviceLease | None) -> bool:
    """True when the ACTIVE lease is the platform return-debt carrier."""
    if lease is None:
        return False
    return (
        lease.lease_type == LeaseType.MAINTENANCE.value
        and lease.reason == DEBT_LEASE_REASON
        and lease.holder == DEBT_LEASE_HOLDER
    )


def map_disarm_result_to_family_state(
    result: str,
    *,
    onboarded: bool,
) -> str:
    """D1.3: map one disarm result to family return state."""
    if result == DISARM_RESULT_CONFIRMED:
        return FAMILY_RETURN_RETURNED
    if result == DISARM_RESULT_EXECUTED_UNVERIFIED:
        return FAMILY_RETURN_UNVERIFIED
    if result == DISARM_RESULT_FAILED:
        # D8.4: non-onboarded failed → unverified (transition posture).
        return FAMILY_RETURN_DEBT if onboarded else FAMILY_RETURN_UNVERIFIED
    if result == DISARM_RESULT_READBACK_FAILED:
        return FAMILY_RETURN_DEBT
    if result in (DISARM_RESULT_NOT_EXECUTED, DISARM_RESULT_INCOMPLETE):
        return FAMILY_RETURN_DEBT
    # Unknown machine value: conservative debt.
    return FAMILY_RETURN_DEBT


def device_aggregate_return_state(return_state: Mapping[str, Any] | None) -> str:
    """D1.4: debt > unverified > returned (missing family counts as unverified)."""
    if not return_state:
        return FAMILY_RETURN_UNVERIFIED
    states = []
    for family in FAMILY_STEPS:
        entry = return_state.get(family)
        if not isinstance(entry, Mapping):
            states.append(FAMILY_RETURN_UNVERIFIED)
            continue
        states.append(str(entry.get("state") or FAMILY_RETURN_UNVERIFIED))
    if FAMILY_RETURN_DEBT in states:
        return FAMILY_RETURN_DEBT
    if FAMILY_RETURN_UNVERIFIED in states:
        return FAMILY_RETURN_UNVERIFIED
    if states and all(s == FAMILY_RETURN_RETURNED for s in states):
        return FAMILY_RETURN_RETURNED
    return FAMILY_RETURN_UNVERIFIED


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _family_onboarded(family: str) -> bool:
    return family in ONBOARDED


def _normalize_disarm_results(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for family, entry in raw.items():
        if family not in FAMILY_STEPS:
            continue
        if isinstance(entry, Mapping):
            result = entry.get("result")
            if isinstance(result, str):
                out[str(family)] = dict(entry)
        elif isinstance(entry, str):
            out[str(family)] = {"result": entry}
    return out


def _pipeline_step_id_to_script(pipeline_def: Any) -> dict[str, str]:
    """Map step_id → script leaf name from a job pipeline_def."""
    mapping: dict[str, str] = {}
    if not isinstance(pipeline_def, Mapping):
        return mapping
    lifecycle = pipeline_def.get("lifecycle")
    stages: Mapping[str, Any]
    if isinstance(lifecycle, Mapping):
        stages = lifecycle
    else:
        stages = pipeline_def
    for stage in ("init", "teardown", "patrol"):
        steps = stages.get(stage) if isinstance(stages, Mapping) else None
        if isinstance(steps, Mapping):
            steps = steps.get("steps") or []
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, Mapping):
                continue
            sid = step.get("step_id") or ""
            script = script_name_from_step(step)
            if sid and script:
                mapping[str(sid)] = script
    return mapping


def _footprint_families_from_traces(
    pipeline_def: Any,
    traces: Iterable[Any],
) -> set[str]:
    """Families with init footprint evidence (any init-stage trace of a footprint script)."""
    id_to_script = _pipeline_step_id_to_script(pipeline_def)
    families: set[str] = set()
    for tr in traces or []:
        stage = getattr(tr, "stage", None) if not isinstance(tr, Mapping) else tr.get("stage")
        if str(stage or "") != "init":
            continue
        step_id = getattr(tr, "step_id", None) if not isinstance(tr, Mapping) else tr.get("step_id")
        script = id_to_script.get(str(step_id or ""), "")
        family = family_for_footprint_script(script)
        if family:
            families.add(family)
    return families


def _footprint_families_for_job_sync(db: Session, job: JobInstance) -> set[str]:
    traces = list(
        db.execute(select(StepTrace).where(StepTrace.job_id == job.id)).scalars().all()
    )
    return _footprint_families_from_traces(getattr(job, "pipeline_def", None), traces)


async def _footprint_families_for_job_async(
    db: AsyncSession, job: JobInstance
) -> set[str]:
    traces = list(
        (
            await db.execute(select(StepTrace).where(StepTrace.job_id == job.id))
        ).scalars().all()
    )
    return _footprint_families_from_traces(getattr(job, "pipeline_def", None), traces)


def _supplement_incomplete_for_footprints(
    results: dict[str, dict[str, Any]],
    footprint_families: set[str],
) -> dict[str, dict[str, Any]]:
    """§3.4.1 / D4.2: no terminal report + footprint → incomplete (do not invent for bare PENDING)."""
    out = dict(results)
    for family in footprint_families:
        if family in out:
            continue
        out[family] = {
            "result": DISARM_RESULT_INCOMPLETE,
            "reason": "no_terminal_report_with_footprint",
        }
    return out


def _recompute_return_state_from_open_debts(
    device: Device,
    open_rows: list[DeviceReturnDebt],
    *,
    now: datetime,
) -> None:
    """D5.10: family state is debt iff an open entry exists for that family."""
    open_families = {row.family for row in open_rows}
    if not open_families and not (device.return_state or {}):
        return
    current = dict(device.return_state or {})
    for family in open_families:
        entry = dict(current.get(family) or {})
        entry["state"] = FAMILY_RETURN_DEBT
        entry["updated_at"] = now.isoformat()
        if "source" not in entry:
            row = next(r for r in open_rows if r.family == family)
            entry["source"] = (
                RETURN_SOURCE_STOCK
                if row.source_kind == _SOURCE_KIND_STOCK
                else RETURN_SOURCE_JOB
            )
        current[family] = entry
    device.return_state = current


def _set_family_return_state(
    device: Device,
    family: str,
    *,
    state: str,
    source: str,
    now: datetime,
    extra: Mapping[str, Any] | None = None,
) -> None:
    current = dict(device.return_state or {})
    entry: dict[str, Any] = {
        "state": state,
        "source": source,
        "updated_at": now.isoformat(),
    }
    if extra:
        entry.update(dict(extra))
    current[family] = entry
    device.return_state = current


# ── sync helpers (recycler / emergency / retirement / init) ───────────────


def _lock_device_sync(db: Session, device_id: int) -> Device | None:
    # populate_existing: callers may already hold a stale Device in the identity
    # map; FOR UPDATE alone does not refresh JSONB return_state (review-r1 F2).
    # Flush first: populate_existing discards unflushed Device edits, and
    # SessionLocal is autoflush=False (#3723 §3.4.3).
    db.flush()
    return (
        db.execute(
            select(Device)
            .where(Device.id == device_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalars().first()


def _open_debts_sync(db: Session, device_id: int) -> list[DeviceReturnDebt]:
    return list(
        db.execute(
            select(DeviceReturnDebt).where(
                DeviceReturnDebt.device_id == device_id,
                DeviceReturnDebt.state == DEBT_STATE_OPEN,
            )
        ).scalars().all()
    )


def open_debts_for_device_sync(db: Session, device_id: int) -> list[DeviceReturnDebt]:
    """Read-only: open debt rows in creation order (ADR-0061 D5.9 / #3723 U6).

    The UI needs a stable oldest-first list; tie-break on id so rows created in
    the same transaction still have a deterministic order.
    """
    return list(
        db.execute(
            select(DeviceReturnDebt)
            .where(
                DeviceReturnDebt.device_id == device_id,
                DeviceReturnDebt.state == DEBT_STATE_OPEN,
            )
            .order_by(DeviceReturnDebt.created_at.asc(), DeviceReturnDebt.id.asc())
        ).scalars().all()
    )


def _active_lease_sync(db: Session, device_id: int) -> DeviceLease | None:
    return (
        db.execute(
            select(DeviceLease)
            .where(
                DeviceLease.device_id == device_id,
                DeviceLease.status == LeaseStatus.ACTIVE.value,
            )
            .with_for_update()
        )
    ).scalars().first()


def _upsert_open_debt_sync(
    db: Session,
    *,
    device_id: int,
    family: str,
    source_kind: str,
    source_job_id: int | None,
    source_fencing_token: str | None,
    cause: str,
    evidence: Mapping[str, Any] | None,
    now: datetime,
) -> DeviceReturnDebt:
    existing = (
        db.execute(
            select(DeviceReturnDebt).where(
                DeviceReturnDebt.device_id == device_id,
                DeviceReturnDebt.family == family,
                DeviceReturnDebt.source_kind == source_kind,
                DeviceReturnDebt.source_job_id.is_(source_job_id)
                if source_job_id is None
                else DeviceReturnDebt.source_job_id == source_job_id,
                DeviceReturnDebt.state == DEBT_STATE_OPEN,
            )
        )
    ).scalars().first()
    if existing is not None:
        # Idempotent replay: keep the open row, refresh evidence lightly.
        if evidence:
            existing.evidence = dict(evidence)
        return existing
    row = DeviceReturnDebt(
        device_id=device_id,
        family=family,
        source_kind=source_kind,
        source_job_id=source_job_id,
        source_fencing_token=source_fencing_token,
        cause=cause,
        state=DEBT_STATE_OPEN,
        created_at=now,
        evidence=dict(evidence) if evidence else None,
    )
    db.add(row)
    db.flush()
    return row


def _apply_job_disarm_to_debt_sync(
    db: Session,
    device: Device,
    job: JobInstance,
    *,
    fencing_token: str | None,
    now: datetime,
) -> int:
    """Write/close debt rows from this job's disarm_results. Returns open count after."""
    results = _normalize_disarm_results(getattr(job, "disarm_results", None))
    footprints = _footprint_families_for_job_sync(db, job)
    results = _supplement_incomplete_for_footprints(results, footprints)
    for family, entry in results.items():
        result = str(entry.get("result") or "")
        onboarded = _family_onboarded(family)
        family_state = map_disarm_result_to_family_state(result, onboarded=onboarded)
        _set_family_return_state(
            device,
            family,
            state=family_state,
            source=RETURN_SOURCE_JOB,
            now=now,
            extra={"job_id": job.id, "disarm_result": result},
        )
        if family_state == FAMILY_RETURN_DEBT:
            _upsert_open_debt_sync(
                db,
                device_id=device.id,
                family=family,
                source_kind=_SOURCE_KIND_JOB,
                source_job_id=job.id,
                source_fencing_token=fencing_token,
                cause=result or DISARM_RESULT_NOT_EXECUTED,
                evidence=entry,
                now=now,
            )
    open_rows = _open_debts_sync(db, device.id)
    _recompute_return_state_from_open_debts(device, open_rows, now=now)
    return len(open_rows)


def _bump_generation_sync(db: Session, device: Device) -> int:
    result = db.execute(
        update(Device)
        .where(Device.id == device.id)
        .values(lease_generation=Device.lease_generation + 1)
        .returning(Device.lease_generation)
    )
    row = result.fetchone()
    assert row is not None
    new_gen = int(row[0])
    device.lease_generation = new_gen
    return new_gen


def _convert_active_job_to_debt_lease_sync(
    db: Session,
    device: Device,
    lease: DeviceLease,
    *,
    now: datetime,
) -> DeviceLease:
    """In-place JOB → MAINTENANCE debt conversion; advances fencing generation."""
    new_gen = _bump_generation_sync(db, device)
    lease.lease_type = LeaseType.MAINTENANCE.value
    lease.job_id = None
    lease.reason = DEBT_LEASE_REASON
    lease.holder = DEBT_LEASE_HOLDER
    lease.agent_instance_id = DEBT_LEASE_AGENT_INSTANCE_ID
    lease.host_id = device.host_id
    lease.lease_generation = new_gen
    lease.fencing_token = f"{device.id}:{new_gen}"
    lease.renewed_at = now
    lease.expires_at = _DEBT_LEASE_EXPIRES_AT
    lease.status = LeaseStatus.ACTIVE.value
    lease.released_at = None
    db.flush()
    return lease


def _acquire_debt_lease_sync(
    db: Session,
    device: Device,
    *,
    now: datetime,
) -> DeviceLease | None:
    """Acquire a debt lease when the device has no ACTIVE lease (init / unretire)."""
    if device.retired_at is not None or not device.host_id:
        return None
    new_gen = _bump_generation_sync(db, device)
    existing = _active_lease_sync(db, device.id)
    if existing is not None:
        return None
    lease = DeviceLease(
        device_id=device.id,
        job_id=None,
        host_id=device.host_id,
        lease_type=LeaseType.MAINTENANCE.value,
        status=LeaseStatus.ACTIVE.value,
        fencing_token=f"{device.id}:{new_gen}",
        lease_generation=new_gen,
        agent_instance_id=DEBT_LEASE_AGENT_INSTANCE_ID,
        reason=DEBT_LEASE_REASON,
        holder=DEBT_LEASE_HOLDER,
        acquired_at=now,
        renewed_at=now,
        expires_at=_DEBT_LEASE_EXPIRES_AT,
    )
    db.add(lease)
    try:
        db.flush()
    except IntegrityError:
        return None
    return lease


def _release_job_lease_sync(
    db: Session, device_id: int, job_id: int
) -> bool:
    now = _now()
    result = db.execute(
        update(DeviceLease)
        .where(
            DeviceLease.device_id == device_id,
            DeviceLease.job_id == job_id,
            DeviceLease.lease_type == LeaseType.JOB.value,
            DeviceLease.status == LeaseStatus.ACTIVE.value,
        )
        .values(status=LeaseStatus.RELEASED.value, released_at=now)
    )
    return bool(result.rowcount)


def handle_job_lease_release_sync(
    db: Session,
    *,
    device_id: int,
    job_id: int,
    job: JobInstance | None = None,
) -> LeaseOutcome:
    """Unified JOB-lease release entry (sync) — recycler / emergency / recovery.

    Must run inside the caller's transaction with any already-held locks.
    Locks the device row, applies disarm→debt mapping when a job is available,
    then either converts to a debt lease or releases the JOB lease.
    """
    now = _now()
    device = _lock_device_sync(db, device_id)
    if device is None:
        return LeaseOutcome(released=False, converted_to_debt=False, open_debt_count=0)

    if job is None:
        job = db.get(JobInstance, job_id)

    lease = _active_lease_sync(db, device_id)
    fencing = lease.fencing_token if lease is not None else None

    open_count = 0
    if job is not None:
        # Nested savepoint so a unique-index race does not poison the outer txn.
        try:
            with db.begin_nested():
                open_count = _apply_job_disarm_to_debt_sync(
                    db, device, job, fencing_token=fencing, now=now
                )
        except IntegrityError:
            open_count = len(_open_debts_sync(db, device_id))
    else:
        open_count = len(_open_debts_sync(db, device_id))

    # Also count pre-existing open debts (other jobs / stock).
    open_count = len(_open_debts_sync(db, device_id))

    if open_count > 0:
        if lease is not None and lease.lease_type == LeaseType.JOB.value and lease.job_id == job_id:
            converted = _convert_active_job_to_debt_lease_sync(db, device, lease, now=now)
            return LeaseOutcome(
                released=False,
                converted_to_debt=True,
                open_debt_count=open_count,
                fencing_token=converted.fencing_token,
            )
        if lease is not None and is_debt_lease(lease):
            return LeaseOutcome(
                released=False,
                converted_to_debt=False,
                open_debt_count=open_count,
                fencing_token=lease.fencing_token,
            )
        if lease is None:
            acquired = _acquire_debt_lease_sync(db, device, now=now)
            return LeaseOutcome(
                released=False,
                converted_to_debt=bool(acquired),
                open_debt_count=open_count,
                fencing_token=acquired.fencing_token if acquired else None,
            )
        # Some other ACTIVE lease (SCRIPT / non-debt MAINTENANCE): leave it;
        # D5.3 says convert when *this* occupancy releases — here we only
        # handle the JOB identified by job_id.
        released = _release_job_lease_sync(db, device_id, job_id)
        return LeaseOutcome(
            released=released,
            converted_to_debt=False,
            open_debt_count=open_count,
        )

    released = _release_job_lease_sync(db, device_id, job_id)
    return LeaseOutcome(
        released=released,
        converted_to_debt=False,
        open_debt_count=0,
    )


# ── async helpers (completion / recovery / reconciler) ────────────────────


async def _lock_device_async(db: AsyncSession, device_id: int) -> Device | None:
    # Same identity-map refresh as sync (review-r1 F2).
    # Flush first for parity with sync / autoflush=False callers (#3723 §3.4.3).
    await db.flush()
    result = await db.execute(
        select(Device)
        .where(Device.id == device_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalars().first()


async def _open_debts_async(db: AsyncSession, device_id: int) -> list[DeviceReturnDebt]:
    result = await db.execute(
        select(DeviceReturnDebt).where(
            DeviceReturnDebt.device_id == device_id,
            DeviceReturnDebt.state == DEBT_STATE_OPEN,
        )
    )
    return list(result.scalars().all())


async def _active_lease_async(db: AsyncSession, device_id: int) -> DeviceLease | None:
    result = await db.execute(
        select(DeviceLease)
        .where(
            DeviceLease.device_id == device_id,
            DeviceLease.status == LeaseStatus.ACTIVE.value,
        )
        .with_for_update()
    )
    return result.scalars().first()


async def _upsert_open_debt_async(
    db: AsyncSession,
    *,
    device_id: int,
    family: str,
    source_kind: str,
    source_job_id: int | None,
    source_fencing_token: str | None,
    cause: str,
    evidence: Mapping[str, Any] | None,
    now: datetime,
) -> DeviceReturnDebt:
    q = select(DeviceReturnDebt).where(
        DeviceReturnDebt.device_id == device_id,
        DeviceReturnDebt.family == family,
        DeviceReturnDebt.source_kind == source_kind,
        DeviceReturnDebt.state == DEBT_STATE_OPEN,
    )
    if source_job_id is None:
        q = q.where(DeviceReturnDebt.source_job_id.is_(None))
    else:
        q = q.where(DeviceReturnDebt.source_job_id == source_job_id)
    existing = (await db.execute(q)).scalars().first()
    if existing is not None:
        if evidence:
            existing.evidence = dict(evidence)
        return existing
    row = DeviceReturnDebt(
        device_id=device_id,
        family=family,
        source_kind=source_kind,
        source_job_id=source_job_id,
        source_fencing_token=source_fencing_token,
        cause=cause,
        state=DEBT_STATE_OPEN,
        created_at=now,
        evidence=dict(evidence) if evidence else None,
    )
    db.add(row)
    await db.flush()
    return row


async def _apply_job_disarm_to_debt_async(
    db: AsyncSession,
    device: Device,
    job: JobInstance,
    *,
    fencing_token: str | None,
    now: datetime,
) -> None:
    results = _normalize_disarm_results(getattr(job, "disarm_results", None))
    footprints = await _footprint_families_for_job_async(db, job)
    results = _supplement_incomplete_for_footprints(results, footprints)
    for family, entry in results.items():
        result = str(entry.get("result") or "")
        onboarded = _family_onboarded(family)
        family_state = map_disarm_result_to_family_state(result, onboarded=onboarded)
        _set_family_return_state(
            device,
            family,
            state=family_state,
            source=RETURN_SOURCE_JOB,
            now=now,
            extra={"job_id": job.id, "disarm_result": result},
        )
        if family_state == FAMILY_RETURN_DEBT:
            await _upsert_open_debt_async(
                db,
                device_id=device.id,
                family=family,
                source_kind=_SOURCE_KIND_JOB,
                source_job_id=job.id,
                source_fencing_token=fencing_token,
                cause=result or DISARM_RESULT_NOT_EXECUTED,
                evidence=entry,
                now=now,
            )
    open_rows = await _open_debts_async(db, device.id)
    _recompute_return_state_from_open_debts(device, open_rows, now=now)


async def _bump_generation_async(db: AsyncSession, device: Device) -> int:
    result = await db.execute(
        update(Device)
        .where(Device.id == device.id)
        .values(lease_generation=Device.lease_generation + 1)
        .returning(Device.lease_generation)
    )
    row = result.fetchone()
    assert row is not None
    new_gen = int(row[0])
    device.lease_generation = new_gen
    return new_gen


async def _convert_active_job_to_debt_lease_async(
    db: AsyncSession,
    device: Device,
    lease: DeviceLease,
    *,
    now: datetime,
) -> DeviceLease:
    new_gen = await _bump_generation_async(db, device)
    lease.lease_type = LeaseType.MAINTENANCE.value
    lease.job_id = None
    lease.reason = DEBT_LEASE_REASON
    lease.holder = DEBT_LEASE_HOLDER
    lease.agent_instance_id = DEBT_LEASE_AGENT_INSTANCE_ID
    lease.host_id = device.host_id
    lease.lease_generation = new_gen
    lease.fencing_token = f"{device.id}:{new_gen}"
    lease.renewed_at = now
    lease.expires_at = _DEBT_LEASE_EXPIRES_AT
    lease.status = LeaseStatus.ACTIVE.value
    lease.released_at = None
    await db.flush()
    return lease


async def _acquire_debt_lease_async(
    db: AsyncSession,
    device: Device,
    *,
    now: datetime,
) -> DeviceLease | None:
    if device.retired_at is not None or not device.host_id:
        return None
    new_gen = await _bump_generation_async(db, device)
    existing = await _active_lease_async(db, device.id)
    if existing is not None:
        return None
    lease = DeviceLease(
        device_id=device.id,
        job_id=None,
        host_id=device.host_id,
        lease_type=LeaseType.MAINTENANCE.value,
        status=LeaseStatus.ACTIVE.value,
        fencing_token=f"{device.id}:{new_gen}",
        lease_generation=new_gen,
        agent_instance_id=DEBT_LEASE_AGENT_INSTANCE_ID,
        reason=DEBT_LEASE_REASON,
        holder=DEBT_LEASE_HOLDER,
        acquired_at=now,
        renewed_at=now,
        expires_at=_DEBT_LEASE_EXPIRES_AT,
    )
    db.add(lease)
    try:
        await db.flush()
    except IntegrityError:
        return None
    return lease


async def _release_job_lease_async(
    db: AsyncSession, device_id: int, job_id: int
) -> bool:
    now = _now()
    result = await db.execute(
        update(DeviceLease)
        .where(
            DeviceLease.device_id == device_id,
            DeviceLease.job_id == job_id,
            DeviceLease.lease_type == LeaseType.JOB.value,
            DeviceLease.status == LeaseStatus.ACTIVE.value,
        )
        .values(status=LeaseStatus.RELEASED.value, released_at=now)
    )
    return bool(result.rowcount)


async def handle_job_lease_release(
    db: AsyncSession,
    *,
    device_id: int,
    job_id: int,
    job: JobInstance | None = None,
) -> LeaseOutcome:
    """Unified JOB-lease release entry (async) — completion / recovery / reconciler."""
    now = _now()
    device = await _lock_device_async(db, device_id)
    if device is None:
        return LeaseOutcome(released=False, converted_to_debt=False, open_debt_count=0)

    if job is None:
        job = await db.get(JobInstance, job_id)

    lease = await _active_lease_async(db, device_id)
    fencing = lease.fencing_token if lease is not None else None

    if job is not None:
        try:
            async with db.begin_nested():
                await _apply_job_disarm_to_debt_async(
                    db, device, job, fencing_token=fencing, now=now
                )
        except IntegrityError:
            pass

    open_count = len(await _open_debts_async(db, device_id))

    if open_count > 0:
        if lease is not None and lease.lease_type == LeaseType.JOB.value and lease.job_id == job_id:
            converted = await _convert_active_job_to_debt_lease_async(
                db, device, lease, now=now
            )
            logger.info(
                "device_return_converted_to_debt device=%s job=%s open=%s token=%s",
                device_id, job_id, open_count, converted.fencing_token,
            )
            return LeaseOutcome(
                released=False,
                converted_to_debt=True,
                open_debt_count=open_count,
                fencing_token=converted.fencing_token,
            )
        if lease is not None and is_debt_lease(lease):
            return LeaseOutcome(
                released=False,
                converted_to_debt=False,
                open_debt_count=open_count,
                fencing_token=lease.fencing_token,
            )
        if lease is None:
            acquired = await _acquire_debt_lease_async(db, device, now=now)
            return LeaseOutcome(
                released=False,
                converted_to_debt=bool(acquired),
                open_debt_count=open_count,
                fencing_token=acquired.fencing_token if acquired else None,
            )
        released = await _release_job_lease_async(db, device_id, job_id)
        return LeaseOutcome(
            released=released,
            converted_to_debt=False,
            open_debt_count=open_count,
        )

    released = await _release_job_lease_async(db, device_id, job_id)
    if released:
        logger.info("device_return_released_job_lease device=%s job=%s", device_id, job_id)
    return LeaseOutcome(
        released=released,
        converted_to_debt=False,
        open_debt_count=0,
    )


# ── discharge / override / late complete ──────────────────────────────────


def _close_family_debts_sync(
    db: Session,
    *,
    device_id: int,
    families: Iterable[str],
    closed_reason: str,
    now: datetime,
) -> int:
    closed = 0
    family_set = set(families)
    for row in _open_debts_sync(db, device_id):
        if row.family not in family_set:
            continue
        row.state = DEBT_STATE_CLOSED
        row.closed_reason = closed_reason
        row.closed_at = now
        closed += 1
    # SessionLocal is autoflush=False: subsequent _open_debts_sync /
    # _maybe_release_debt_lease_sync must see the closed rows (review-r1 F1).
    if closed:
        db.flush()
    return closed


def _maybe_release_debt_lease_sync(db: Session, device: Device, *, now: datetime) -> bool:
    if _open_debts_sync(db, device.id):
        return False
    lease = _active_lease_sync(db, device.id)
    if not is_debt_lease(lease):
        return False
    assert lease is not None
    lease.status = LeaseStatus.RELEASED.value
    lease.released_at = now
    db.flush()
    return True


def apply_discharge_sync(
    db: Session,
    *,
    device_id: int,
    fencing_token: str,
    family_results: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Agent discharge report (D5.5). Validates debt-lease token; partial ok."""
    now = _now()
    device = _lock_device_sync(db, device_id)
    if device is None:
        return {"ok": False, "code": "DEVICE_NOT_FOUND"}
    lease = _active_lease_sync(db, device_id)
    if not is_debt_lease(lease) or lease is None or lease.fencing_token != fencing_token:
        return {"ok": False, "code": "DEBT_TOKEN_INVALID"}

    discharged_families: list[str] = []
    rejected_families: list[dict] = []
    accepted_count = 0
    for family, entry in family_results.items():
        if family not in FAMILY_STEPS:
            continue
        result = str(entry.get("result") or "")
        policy = evaluate_probe_entry(device, family, entry, token_valid=True)
        decision = probe_entry_response(family, entry, policy, entry_point="discharge")
        if decision["status_code"] == 422:
            audit_unverified_probe(db, resource_id=device_id, family=family, entry=entry, policy=policy, entry_point="discharge",
                                   report_identity=hashlib.sha256(fencing_token.encode()).hexdigest())
            rejected_families.append(decision)
            continue
        if policy["trusted"]:
            result = policy["result"]
        accepted_count += 1
        onboarded = _family_onboarded(family)
        family_state = map_disarm_result_to_family_state(result, onboarded=onboarded)
        _set_family_return_state(
            device,
            family,
            state=family_state,
            source=RETURN_SOURCE_DISCHARGE,
            now=now,
            extra={"disarm_result": result},
        )
        # Successful discharge for a family closes ALL open debts of that family.
        if policy["trusted"] and result == DISARM_RESULT_CONFIRMED:
            _close_family_debts_sync(
                db,
                device_id=device_id,
                families=[family],
                closed_reason=DEBT_CLOSE_DISCHARGED,
                now=now,
            )
            discharged_families.append(family)

    if rejected_families and not accepted_count:
        return {"ok": True, "discharged_families": [], "debt_lease_released": False,
                "open_debt_count": len(_open_debts_sync(db, device_id)),
                "fencing_token": lease.fencing_token, "rejected_families": rejected_families}
    released = _maybe_release_debt_lease_sync(db, device, now=now)
    open_rows = _open_debts_sync(db, device_id)
    _recompute_return_state_from_open_debts(device, open_rows, now=now)
    open_left = len(open_rows)
    return {
        "ok": True,
        "discharged_families": discharged_families,
        "debt_lease_released": released,
        "open_debt_count": open_left,
        "fencing_token": None if released else lease.fencing_token,
        "rejected_families": rejected_families,
    }


def apply_override_sync(
    db: Session,
    *,
    device_id: int,
    families: Iterable[str] | None,
    reason_category: str,
    evidence_ref: str,
) -> dict[str, Any]:
    """Admin override (D5.7): close selected families as unverified/override."""
    now = _now()
    device = _lock_device_sync(db, device_id)
    if device is None:
        return {"ok": False, "code": "DEVICE_NOT_FOUND"}
    target = list(families) if families else list(FAMILY_STEPS.keys())
    for family in target:
        if family not in FAMILY_STEPS:
            continue
        _set_family_return_state(
            device,
            family,
            state=FAMILY_RETURN_UNVERIFIED,
            source=RETURN_SOURCE_OVERRIDE,
            now=now,
            extra={
                "reason_category": reason_category,
                "evidence_ref": evidence_ref,
            },
        )
    closed = _close_family_debts_sync(
        db,
        device_id=device_id,
        families=target,
        closed_reason=DEBT_CLOSE_OVERRIDDEN,
        now=now,
    )
    released = _maybe_release_debt_lease_sync(db, device, now=now)
    open_rows = _open_debts_sync(db, device_id)
    _recompute_return_state_from_open_debts(device, open_rows, now=now)
    return {
        "ok": True,
        "families": target,
        "closed_count": closed,
        "debt_lease_released": released,
        "open_debt_count": len(open_rows),
    }


def apply_late_confirmed_sync(
    db: Session,
    *,
    device_id: int,
    job_id: int,
    fencing_token: str,
    confirmed_families: Iterable[str],
) -> dict[str, Any]:
    """D5.6: late complete with confirmed may close same-source open debts."""
    now = _now()
    device = _lock_device_sync(db, device_id)
    if device is None:
        return {"ok": False, "code": "DEVICE_NOT_FOUND"}
    closed = 0
    touched: set[str] = set()
    for family in confirmed_families:
        rows = [
            r for r in _open_debts_sync(db, device_id)
            if r.family == family
            and r.source_kind == _SOURCE_KIND_JOB
            and r.source_job_id == job_id
            and r.source_fencing_token == fencing_token
        ]
        for row in rows:
            row.state = DEBT_STATE_CLOSED
            row.closed_reason = DEBT_CLOSE_DISCHARGED
            row.closed_at = now
            closed += 1
            touched.add(family)
        if family in touched:
            _set_family_return_state(
                device,
                family,
                state=FAMILY_RETURN_RETURNED if _family_onboarded(family) else FAMILY_RETURN_UNVERIFIED,
                source=RETURN_SOURCE_JOB,
                now=now,
                extra={"job_id": job_id, "late_confirmed": True},
            )
    if closed:
        db.flush()
    released = _maybe_release_debt_lease_sync(db, device, now=now)
    open_rows = _open_debts_sync(db, device_id)
    _recompute_return_state_from_open_debts(device, open_rows, now=now)
    return {
        "ok": True,
        "closed_count": closed,
        "debt_lease_released": released,
        "open_debt_count": len(open_rows),
    }


async def apply_late_confirmed(
    db: AsyncSession,
    *,
    device_id: int,
    job_id: int,
    fencing_token: str,
    confirmed_families: Iterable[str],
) -> dict[str, Any]:
    """Async D5.6 late-confirmed closer (wired from ``/complete``)."""
    now = _now()
    device = await _lock_device_async(db, device_id)
    if device is None:
        return {"ok": False, "code": "DEVICE_NOT_FOUND"}
    closed = 0
    touched: set[str] = set()
    open_rows = await _open_debts_async(db, device_id)
    for family in confirmed_families:
        rows = [
            r for r in open_rows
            if r.family == family
            and r.source_kind == _SOURCE_KIND_JOB
            and r.source_job_id == job_id
            and r.source_fencing_token == fencing_token
        ]
        for row in rows:
            row.state = DEBT_STATE_CLOSED
            row.closed_reason = DEBT_CLOSE_DISCHARGED
            row.closed_at = now
            closed += 1
            touched.add(family)
        if family in touched:
            _set_family_return_state(
                device,
                family,
                state=FAMILY_RETURN_RETURNED if _family_onboarded(family) else FAMILY_RETURN_UNVERIFIED,
                source=RETURN_SOURCE_JOB,
                now=now,
                extra={"job_id": job_id, "late_confirmed": True},
            )
    if closed:
        await db.flush()
    # Release debt lease if no open rows remain.
    open_rows = await _open_debts_async(db, device_id)
    released = False
    if not open_rows:
        lease = await _active_lease_async(db, device_id)
        if is_debt_lease(lease) and lease is not None:
            lease.status = LeaseStatus.RELEASED.value
            lease.released_at = now
            await db.flush()
            released = True
    open_rows = await _open_debts_async(db, device_id)
    _recompute_return_state_from_open_debts(device, open_rows, now=now)
    return {
        "ok": True,
        "closed_count": closed,
        "debt_lease_released": released,
        "open_debt_count": len(open_rows),
    }


async def try_apply_late_confirmed(
    db: AsyncSession,
    *,
    device_id: int,
    job_id: int,
    fencing_token: str,
    disarm_results: Any,
) -> dict[str, Any] | None:
    """Return late-confirmed outcome when D5.6 source checks pass; else None.

    Strict: only closes JOB-sourced open rows whose ``source_fencing_token``
    exactly matches the caller token. Does not relax unrelated stale-token rules.
    """
    results = _normalize_disarm_results(disarm_results)
    confirmed = [
        family
        for family, entry in results.items()
        if str(entry.get("result") or "") == DISARM_RESULT_CONFIRMED
    ]
    if not confirmed:
        return None
    open_rows = await _open_debts_async(db, device_id)
    matching = [
        row
        for row in open_rows
        if row.family in confirmed
        and row.source_kind == _SOURCE_KIND_JOB
        and row.source_job_id == job_id
        and row.source_fencing_token == fencing_token
    ]
    if not matching:
        return None
    device = await _lock_device_async(db, device_id)
    job = await db.get(JobInstance, job_id)
    if device is None or job is None:
        return None
    verified = []
    for family in confirmed:
        policy = evaluate_probe_entry(device, family, results[family], token_valid=True, job=job)
        decision = probe_entry_response(family, results[family], policy, entry_point="late")
        if not decision["ignore"] and policy["trusted"] and policy["result"] == DISARM_RESULT_CONFIRMED:
            verified.append(family)
        else:
            await audit_unverified_probe_async(
                db, resource_id=job_id, family=family, entry=results[family], policy=policy,
                entry_point="late", resource_type="job_instance",
            )
    if not verified:
        return {"ok": True, "closed_count": 0, "debt_lease_released": False,
                "open_debt_count": len(open_rows)}
    return await apply_late_confirmed(
        db,
        device_id=device_id,
        job_id=job_id,
        fencing_token=fencing_token,
        confirmed_families=verified,
    )


# ── retirement (D5.8) ─────────────────────────────────────────────────────


def release_debt_lease_for_retirement_sync(db: Session, device: Device) -> bool:
    """Release debt lease inside retire txn; open debts stay open."""
    now = _now()
    lease = _active_lease_sync(db, device.id)
    if not is_debt_lease(lease):
        return False
    assert lease is not None
    lease.status = LeaseStatus.RELEASED.value
    lease.released_at = now
    db.flush()
    return True


def restore_debt_lease_on_unretire_sync(db: Session, device: Device) -> DeviceLease | None:
    """If open debts remain after unretire, rebuild debt lease in same txn."""
    if not _open_debts_sync(db, device.id):
        return None
    return _acquire_debt_lease_sync(db, device, now=_now())


def has_only_debt_lease_sync(db: Session, device_id: int) -> bool:
    lease = (
        db.execute(
            select(DeviceLease).where(
                DeviceLease.device_id == device_id,
                DeviceLease.status == LeaseStatus.ACTIVE.value,
            )
        )
    ).scalars().first()
    return is_debt_lease(lease)


# ── admission / heartbeat helpers ─────────────────────────────────────────


def devices_with_open_debt_sync(db: Session, device_ids: Iterable[int]) -> set[int]:
    ids = list(device_ids)
    if not ids:
        return set()
    rows = db.execute(
        select(DeviceReturnDebt.device_id).where(
            DeviceReturnDebt.device_id.in_(ids),
            DeviceReturnDebt.state == DEBT_STATE_OPEN,
        ).distinct()
    ).all()
    return {int(r[0]) for r in rows}


def open_debts_for_host_sync(db: Session, host_id: str) -> list[dict[str, Any]]:
    """Heartbeat ``return_debts`` payload for online devices on this host."""
    rows = (
        db.execute(
            select(DeviceReturnDebt, Device, DeviceLease)
            .join(Device, Device.id == DeviceReturnDebt.device_id)
            .outerjoin(
                DeviceLease,
                (DeviceLease.device_id == Device.id)
                & (DeviceLease.status == LeaseStatus.ACTIVE.value),
            )
            .where(
                Device.host_id == host_id,
                Device.retired_at.is_(None),
                DeviceReturnDebt.state == DEBT_STATE_OPEN,
            )
        )
    ).all()
    # Group by device
    by_device: dict[int, dict[str, Any]] = {}
    for debt, device, lease in rows:
        if not is_debt_lease(lease):
            continue
        bucket = by_device.setdefault(
            device.id,
            {
                "device_id": device.id,
                "serial": device.serial,
                "fencing_token": lease.fencing_token,
                "families": [],
            },
        )
        if debt.family not in bucket["families"]:
            bucket["families"].append(debt.family)
    return list(by_device.values())


# ── init (D1.7 / D5.11) ───────────────────────────────────────────────────


def _history_disarm_results_for_job_sync(
    db: Session,
    job: JobInstance,
) -> dict[str, dict[str, Any]]:
    """§3.0.6 / §3.4.1.2: prefer stored disarm_results; else derive from StepTraces.

    Pre-stage-A jobs often have NULL ``disarm_results`` (nullable column, no
    backfill). Successful teardown COMPLETED must map to executed_unverified,
    not incomplete-from-footprint-only.
    """
    results = _normalize_disarm_results(getattr(job, "disarm_results", None))
    if results:
        return results
    traces = list(
        db.execute(select(StepTrace).where(StepTrace.job_id == job.id)).scalars().all()
    )
    derived = derive_disarm_results_from_traces(
        getattr(job, "pipeline_def", None), traces
    )
    return _normalize_disarm_results(derived)


def _history_result_for_family_sync(
    db: Session,
    *,
    device_id: int,
    family: str,
) -> tuple[JobInstance | None, str | None]:
    """D1.7: latest ended job that left a footprint or reported/derived disarm for family.

    Jobs are scanned newest-first by ``ended_at``, so a later successful disarm
    covers older residue, and a later failure is not shadowed by an older success.
    """
    jobs = list(
        db.execute(
            select(JobInstance)
            .where(
                JobInstance.device_id == device_id,
                JobInstance.ended_at.is_not(None),
            )
            .order_by(JobInstance.ended_at.desc(), JobInstance.id.desc())
        ).scalars().all()
    )
    for job in jobs:
        results = _history_disarm_results_for_job_sync(db, job)
        if family in results:
            return job, str(results[family].get("result") or "") or None
        footprints = _footprint_families_for_job_sync(db, job)
        if family in footprints:
            # Footprint with neither stored nor derived disarm evidence → incomplete.
            return job, DISARM_RESULT_INCOMPLETE
    return None, None


def init_device_return_sync(
    db: Session,
    *,
    device_id: int,
    dry_run: bool = True,
) -> dict[str, Any]:
    """One-shot / repeatable stage-B init for a single device (D1.7 / D5.11)."""
    now = _now()
    device = _lock_device_sync(db, device_id)
    if device is None:
        return {"device_id": device_id, "status": "not_found"}
    if device.retired_at is not None:
        return {"device_id": device_id, "status": "retired_skipped"}

    summary: dict[str, Any] = {
        "device_id": device_id,
        "serial": device.serial,
        "families": {},
        "acquired_debt_lease": False,
        "dry_run": dry_run,
    }

    current = dict(device.return_state or {})
    changed = False
    for family in FAMILY_STEPS:
        if family in current and isinstance(current[family], Mapping):
            summary["families"][family] = dict(current[family])
            continue

        if not _family_onboarded(family):
            # Non-onboarded: stock unverified only — never returned (D1.7 / D8.7).
            entry = {
                "state": FAMILY_RETURN_UNVERIFIED,
                "source": RETURN_SOURCE_STOCK,
                "updated_at": now.isoformat(),
            }
            summary["families"][family] = entry
            if not dry_run:
                current[family] = entry
                changed = True
            continue

        hist_job, hist_result = _history_result_for_family_sync(
            db, device_id=device_id, family=family
        )
        if hist_job is None or not hist_result:
            entry = {
                "state": FAMILY_RETURN_UNVERIFIED,
                "source": RETURN_SOURCE_STOCK,
                "updated_at": now.isoformat(),
            }
            summary["families"][family] = entry
            if not dry_run:
                current[family] = entry
                changed = True
            continue

        family_state = map_disarm_result_to_family_state(hist_result, onboarded=True)
        # Historical jobs lack verified-probe support → never initialize as returned.
        if family_state == FAMILY_RETURN_RETURNED:
            family_state = FAMILY_RETURN_UNVERIFIED
        entry = {
            "state": family_state,
            "source": RETURN_SOURCE_STOCK,
            "updated_at": now.isoformat(),
            "history_job_id": hist_job.id,
            "disarm_result": hist_result,
        }
        summary["families"][family] = entry
        if not dry_run:
            current[family] = entry
            changed = True
            if family_state == FAMILY_RETURN_DEBT:
                _upsert_open_debt_sync(
                    db,
                    device_id=device_id,
                    family=family,
                    source_kind=_SOURCE_KIND_STOCK,
                    source_job_id=hist_job.id,
                    source_fencing_token=None,
                    cause=hist_result,
                    evidence={"history_job_id": hist_job.id, "disarm_result": hist_result},
                    now=now,
                )

    if not dry_run and changed:
        device.return_state = current

    open_rows = _open_debts_sync(db, device_id)
    if not dry_run:
        _recompute_return_state_from_open_debts(device, open_rows, now=now)
        # Refresh summary families from recomputed state.
        for family, entry in dict(device.return_state or {}).items():
            if isinstance(entry, Mapping):
                summary["families"][family] = dict(entry)

    lease = _active_lease_sync(db, device_id)
    needs_debt = bool(open_rows) or any(
        (summary["families"].get(f) or {}).get("state") == FAMILY_RETURN_DEBT
        for f in FAMILY_STEPS
    )
    if needs_debt:
        if lease is None and not dry_run:
            acquired = _acquire_debt_lease_sync(db, device, now=now)
            summary["acquired_debt_lease"] = bool(acquired)
            if acquired is None:
                summary["status"] = "lease_conflict_retry"
                return summary
        elif lease is not None and not is_debt_lease(lease):
            summary["status"] = "occupied_debt_recorded"
            summary["open_debt_count"] = len(open_rows)
            return summary

    summary["status"] = "ok"
    summary["open_debt_count"] = (
        len(_open_debts_sync(db, device_id)) if not dry_run else len(open_rows)
    )
    return summary


def refresh_device_return_debt_gauges(db: Session) -> None:
    """Fact producer for ``stp_device_return_debt_*`` (D7.2 / §3.4.1 #5).

    Called from ``/metrics`` scrape — queries open debt rows and writes gauges.
    """
    try:
        rows = list(
            db.execute(
                select(DeviceReturnDebt).where(
                    DeviceReturnDebt.state == DEBT_STATE_OPEN
                )
            ).scalars().all()
        )
    except Exception:
        logger.warning("device_return_debt_gauge_refresh_failed", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return

    counts: dict[str, int] = {family: 0 for family in FAMILY_STEPS}
    oldest: datetime | None = None
    for row in rows:
        fam = row.family if row.family in counts else "unknown"
        counts[fam] = counts.get(fam, 0) + 1
        created = row.created_at
        if created is not None:
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if oldest is None or created < oldest:
                oldest = created
    for family, count in counts.items():
        set_device_return_debt_open(family, count)
    if "unknown" in counts:
        set_device_return_debt_open("unknown", counts["unknown"])
    if oldest is None:
        set_device_return_debt_oldest_seconds(0.0)
    else:
        age = max(0.0, (_now() - oldest).total_seconds())
        set_device_return_debt_oldest_seconds(age)


# B6 U8: DB adapters for the shared pure policies. No consumer imports
# agent_completion (C5); AsyncSession callers use run_sync for identical reads.
def manifest_package_sha(script_name: str, version: str) -> str | None:
    """Resolve append-only identity, including retired historical versions."""

    try:
        doc = json.loads((Path(__file__).resolve().parents[2] / "tool_manifest.json").read_text())
        versions = doc["tools"][script_name]["versions"]
        return next((v.get("package_sha256") for v in versions if v.get("version") == version), None)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _u8_timestamp(value: Any) -> float | None:
    if not isinstance(value, datetime):
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp()


def guard_owner_valid(db: Session, job: JobInstance, now: datetime, guard: Mapping[str, Any]) -> bool:

    # Columns bypass the ORM identity map: each invocation sees latest committed
    # facts under READ COMMITTED after acquiring the job row lock.
    row = db.execute(select(
        DeviceLease.job_id, DeviceLease.fencing_token, DeviceLease.agent_instance_id,
        DeviceLease.host_id, DeviceLease.lease_type, DeviceLease.expires_at,
        Host.last_heartbeat, Host.boot_id, Host.last_agent_instance_id,
        Device.host_id.label("device_host_id"),
    ).select_from(DeviceLease).join(Host, Host.id == DeviceLease.host_id)
      .join(Device, Device.id == DeviceLease.device_id).where(
        DeviceLease.device_id == job.device_id,
        DeviceLease.status == LeaseStatus.ACTIVE.value,
    )).first()
    stamp = now.timestamp()
    return bool(row and row.boot_id and row.last_agent_instance_id
                and row.job_id == job.id and row.lease_type == LeaseType.JOB.value
                and row.host_id == job.host_id == row.device_host_id
                and row.fencing_token == guard.get("fencing_token")
                and row.agent_instance_id == guard.get("agent_instance_id") == row.last_agent_instance_id
                and row.boot_id == guard.get("boot_id")
                and _u8_timestamp(row.expires_at) is not None and _u8_timestamp(row.expires_at) > stamp
                and _u8_timestamp(row.last_heartbeat) is not None
                and 0 <= stamp - _u8_timestamp(row.last_heartbeat) < HOST_HEARTBEAT_TIMEOUT_SECONDS)


def job_guard_deadline(db: Session, job: JobInstance, now: datetime) -> datetime | None:

    if job.status != "RUNNING":
        return None
    guard = job.disarm_guard
    if not isinstance(guard, Mapping):
        return None
    if now.timestamp() < guard.get("last_server_at", now.timestamp()):
        job.disarm_guard = {**guard, "reason": "clock_regressed"}
        return None
    stamp = disarm_guard_deadline(
        guard, now=now.timestamp(), owner_valid=guard_owner_valid(db, job, now, guard),
        execution_heartbeat_at=_u8_timestamp(job.last_execution_heartbeat_at),
        execution_heartbeat_timeout_seconds=running_heartbeat_timeout_seconds(job),
    )
    return datetime.fromtimestamp(stamp, timezone.utc) if stamp is not None else None


def accept_job_disarm_evidence(
    db: Session, job: JobInstance, evidence: Mapping[str, Any], *,
    host_id: str, agent_instance_id: str, now: datetime, via_coordinator: bool,
) -> dict | None:

    if job.status != "RUNNING" or job.host_id != host_id:
        return None
    if evidence.get("protocol") != 1 or evidence.get("phase") not in ("queue", "execute"):
        return None
    if type(evidence.get("seq")) is not int or evidence["seq"] < 0:
        return None
    guard = job.disarm_guard
    if guard is None:
        host_row = db.execute(select(Host.boot_id).where(Host.id == host_id)).first()
        context = db.execute(select(PlanRun.run_context).where(PlanRun.id == job.plan_run_id)).scalar_one_or_none()
        abort_at, _ = abort_reap_clock(context, job.id, host_id)
        lifecycle = (job.pipeline_def or {}).get("lifecycle") or {}
        steps = lifecycle.get("teardown") or []
        if isinstance(steps, dict):
            steps = steps.get("steps") or []
        if evidence.get("disarm_only") is True:
            # manual_exit path: selected obligations are frozen as a whole.
            footprint = [family_for_footprint_script(script_name_from_step(s))
                         for s in lifecycle.get("init", []) if isinstance(s, dict)]
            steps = select_disarm_steps(steps, [f for f in footprint if f])
        resolved = [{**s, "package_sha256": manifest_package_sha(
            script_name_from_step(s), str(s.get("version") or ""))} for s in steps if isinstance(s, dict)]
        guard = freeze_disarm_guard(
            resolved, now=now.timestamp(), abort_at=_u8_timestamp(abort_at),
            fencing_token=str(evidence.get("fencing_token") or ""), agent_instance_id=agent_instance_id,
        )
        guard["boot_id"] = host_row[0] if host_row else None
    # A same-boot recovery may rotate the valid lease token. Rebind ownership,
    # retaining every frozen deadline, consumed queue second and sequence.
    rebound = {**guard, "fencing_token": str(evidence.get("fencing_token") or ""),
               "agent_instance_id": agent_instance_id}
    if not guard_owner_valid(db, job, now, rebound):
        return None
    guard = rebound
    # No protection budget can be recreated after restart or token rotation.
    guard = receive_disarm_evidence(guard, evidence, now=now.timestamp(), via_coordinator=via_coordinator)
    if guard.get("phase") is None:
        return None
    job.disarm_guard = guard
    flag_modified(job, "updated_at")  # stage evidence is not business progress
    if guard.get("phase") == "execute" and guard.get("progress_seen_at") == now.timestamp():
        # A newly accepted real script PROGRESS also proves the executor is
        # alive. A coordinator heartbeat carrying only an old marker does not.
        job.last_execution_heartbeat_at = now
        job.last_progress_at = now
    return guard


def evaluate_probe_entry(
    device: Device, family: str, entry: Mapping[str, Any], *,
    token_valid: bool, job: JobInstance | None = None,
) -> dict[str, Any]:

    evidence = entry.get("evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    script = str(evidence.get("script_name") or "")
    version = str(evidence.get("script_version") or "")
    if job is not None:
        stages = (job.pipeline_def or {}).get("lifecycle") or {}
        steps = stages.get("teardown") or []
        if isinstance(steps, dict):
            steps = steps.get("steps") or []
        step = next((s for s in steps if isinstance(s, dict)
                     and str(s.get("step_id") or script_name_from_step(s)) == entry.get("step_id")), {})
        script, version = script_name_from_step(step), step_version_of(step)
    return probe_result_policy(
        family, entry, platform=device.platform, model=device.model,
        script_name=script, script_version=version,
        package_sha256=manifest_package_sha(script, version), token_valid=token_valid,
    )


def _probe_audit_details(family: str, entry: Mapping, policy: Mapping, *, entry_point: str,
                         replacement_result: str | None, report_identity: str | None = None) -> dict:
    reports = policy.get("report_ref") or [c.get("report_ref") for c in policy.get("expected", []) if c.get("report_ref")]
    details = {"entry_point": entry_point, "family": family, "reported_result": entry.get("result"),
               "replacement_result": replacement_result,
               "reason": policy.get("reason") or "raw_result_mismatch", "report_ref": reports,
               "raw": dict(entry), "expected": policy.get("expected"), "observed": policy.get("observed")}
    details["report_identity"] = report_identity
    details["report_digest"] = hashlib.sha256(json.dumps(details, sort_keys=True, default=str).encode()).hexdigest()
    return details


def audit_unverified_probe(db: Session, *, resource_id: int, family: str, entry: Mapping, policy: Mapping,
                           entry_point: str, replacement_result: str | None = None,
                           resource_type: str = "device", report_identity: str | None = None) -> None:
    """v1.7.12: one diagnostic row per item; same read-only replay adds none.

    Use the existing audit writer's synchronous equivalent for sync services.
    Independent transaction survives the caller's business rollback on 422.
    Callers serialize on the existing job/device lock before this operation.
    """
    details = _probe_audit_details(family, entry, policy, entry_point=entry_point, replacement_result=replacement_result, report_identity=report_identity)
    with Session(bind=db.get_bind()) as diagnostic:
        duplicate = diagnostic.execute(select(AuditLog.id).where(
            AuditLog.action == "unverified_probe_result", AuditLog.resource_type == resource_type,
            AuditLog.resource_id == str(resource_id),
            AuditLog.details["report_digest"].as_string() == details["report_digest"],
        )).first()
        if not duplicate:
            record_audit(diagnostic, action="unverified_probe_result", resource_type=resource_type,
                         resource_id=resource_id, username="agent", details=details, strict=True)
            diagnostic.commit()


async def audit_unverified_probe_async(db: AsyncSession, *, resource_id: int, family: str, entry: Mapping,
                                       policy: Mapping, entry_point: str, resource_type: str = "device") -> None:
    """Async entry uses record_audit_async; same audit/count and replay key."""
    details = _probe_audit_details(family, entry, policy, entry_point=entry_point, replacement_result=None)
    async with AsyncSession(bind=db.bind) as diagnostic:
        duplicate = (await diagnostic.execute(select(AuditLog.id).where(
            AuditLog.action == "unverified_probe_result", AuditLog.resource_type == resource_type,
            AuditLog.resource_id == str(resource_id),
            AuditLog.details["report_digest"].as_string() == details["report_digest"],
        ))).first()
        if not duplicate:
            await record_audit_async(diagnostic, action="unverified_probe_result", resource_type=resource_type,
                                     resource_id=resource_id, username="agent", details=details)
            await diagnostic.commit()


def check_init_probe_result(db: Session, device: Device, family: str, entry: Mapping, *, token_valid: bool) -> dict:
    """Shared init behavior only; U4 supplies the real token-protected caller."""
    if not token_valid:
        return {"status_code": 409, "code": "DEBT_TOKEN_INVALID"}
    policy = evaluate_probe_entry(device, family, entry, token_valid=token_valid)
    decision = probe_entry_response(family, entry, policy, entry_point="init_check")
    if decision["status_code"] == 422:
        audit_unverified_probe(db, resource_id=device.id, family=family, entry=entry, policy=policy, entry_point="init_check")
    return decision


def verify_job_results(
    db: Session, job: JobInstance, reported: Mapping[str, Mapping[str, Any]],
    derived: Mapping[str, Mapping[str, Any]], *, token_valid: bool,
) -> dict[str, dict]:

    device = db.execute(select(Device).where(Device.id == job.device_id)
                        .execution_options(populate_existing=True)).scalars().one()
    results = {f: dict(e) for f, e in derived.items()}
    for family, entry in reported.items():
        if family not in FAMILY_STEPS:
            continue
        current = dict(entry)
        if current.get("exit_code") in PLATFORM_INTERRUPTED_EXIT_CODES:
            current["result"] = DISARM_RESULT_INCOMPLETE
        else:
            policy = evaluate_probe_entry(device, family, current, token_valid=token_valid, job=job)
            if probe_entry_response(family, current, policy, entry_point="complete")["use_trace"]:
                replacement = dict(derived.get(family) or {"result": DISARM_RESULT_NOT_EXECUTED})
                audit_unverified_probe(db, resource_id=job.id, family=family, entry=current, policy=policy,
                                       entry_point="complete", replacement_result=replacement["result"], resource_type="job_instance")
                current = replacement
            elif policy["trusted"]:
                current["result"] = policy["result"]
            # Non-privileged values carry evidence but do not close a debt.
        results[family] = current
    return results
