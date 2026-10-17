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
)
from backend.models.device_lease import DeviceLease
from backend.models.device_return_debt import DeviceReturnDebt
from backend.models.enums import LeaseStatus, LeaseType
from backend.models.host import Device
from backend.models.job import JobInstance

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
    return (
        db.execute(select(Device).where(Device.id == device_id).with_for_update())
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
    return len(_open_debts_sync(db, device.id))


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
    result = await db.execute(
        select(Device).where(Device.id == device_id).with_for_update()
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
    for family, entry in family_results.items():
        if family not in FAMILY_STEPS:
            continue
        result = str(entry.get("result") or "")
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
        if family_state in (FAMILY_RETURN_RETURNED, FAMILY_RETURN_UNVERIFIED) and result in (
            DISARM_RESULT_CONFIRMED,
            DISARM_RESULT_EXECUTED_UNVERIFIED,
        ):
            _close_family_debts_sync(
                db,
                device_id=device_id,
                families=[family],
                closed_reason=DEBT_CLOSE_DISCHARGED,
                now=now,
            )
            discharged_families.append(family)

    released = _maybe_release_debt_lease_sync(db, device, now=now)
    open_left = len(_open_debts_sync(db, device_id))
    return {
        "ok": True,
        "discharged_families": discharged_families,
        "debt_lease_released": released,
        "open_debt_count": open_left,
        "fencing_token": None if released else lease.fencing_token,
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
    return {
        "ok": True,
        "families": target,
        "closed_count": closed,
        "debt_lease_released": released,
        "open_debt_count": len(_open_debts_sync(db, device_id)),
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
    for family in confirmed_families:
        rows = [
            r for r in _open_debts_sync(db, device_id)
            if r.family == family
            and r.source_kind == _SOURCE_KIND_JOB
            and r.source_job_id == job_id
            and (not r.source_fencing_token or r.source_fencing_token == fencing_token)
        ]
        for row in rows:
            row.state = DEBT_STATE_CLOSED
            row.closed_reason = DEBT_CLOSE_DISCHARGED
            row.closed_at = now
            closed += 1
            _set_family_return_state(
                device,
                family,
                state=FAMILY_RETURN_RETURNED if _family_onboarded(family) else FAMILY_RETURN_UNVERIFIED,
                source=RETURN_SOURCE_JOB,
                now=now,
                extra={"job_id": job_id, "late_confirmed": True},
            )
    released = _maybe_release_debt_lease_sync(db, device, now=now)
    return {
        "ok": True,
        "closed_count": closed,
        "debt_lease_released": released,
        "open_debt_count": len(_open_debts_sync(db, device_id)),
    }


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


def init_device_return_sync(
    db: Session,
    *,
    device_id: int,
    dry_run: bool = True,
) -> dict[str, Any]:
    """One-shot / repeatable stage-B init for a single device."""
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

    # For each family: find latest job with disarm_results / footprint evidence.
    # Conservative without full history scan: if return_state already set, keep
    # idempotent; otherwise mark unverified/stock (ONBOARDED empty ⇒ no confirmed).
    current = dict(device.return_state or {})
    changed = False
    for family in FAMILY_STEPS:
        if family in current and isinstance(current[family], Mapping):
            summary["families"][family] = dict(current[family])
            continue
        # Default stock unverified — never initialize as returned (D1.7).
        entry = {
            "state": FAMILY_RETURN_UNVERIFIED,
            "source": RETURN_SOURCE_STOCK,
            "updated_at": now.isoformat(),
        }
        summary["families"][family] = entry
        if not dry_run:
            current[family] = entry
            changed = True

    open_before = len(_open_debts_sync(db, device_id))
    if not dry_run and changed:
        device.return_state = current

    lease = _active_lease_sync(db, device_id)
    if open_before > 0 or any(
        (summary["families"].get(f) or {}).get("state") == FAMILY_RETURN_DEBT
        for f in FAMILY_STEPS
    ):
        if lease is None and not dry_run:
            acquired = _acquire_debt_lease_sync(db, device, now=now)
            summary["acquired_debt_lease"] = bool(acquired)
            if acquired is None:
                # Claim won the race — caller should retry as occupied path.
                summary["status"] = "lease_conflict_retry"
                return summary
        elif lease is not None and not is_debt_lease(lease):
            summary["status"] = "occupied_debt_recorded"
            return summary

    summary["status"] = "ok"
    summary["open_debt_count"] = len(_open_debts_sync(db, device_id)) if not dry_run else open_before
    return summary
