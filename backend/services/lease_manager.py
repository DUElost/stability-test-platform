"""Device Lease Manager — ADR-0019 Phase 1.

Provides atomic acquire/release for device leases with full fencing-token
semantics.  Each successful acquire increments device.lease_generation and
records a snapshot of the new generation in device_leases.

Phase 1: standalone module — does NOT replace device_lock.py or modify
          the claim_jobs flow in agent_api.py.
Phase 2+: integrated into claim with FOR UPDATE SKIP LOCKED patterns.
Phase 2c: device_leases becomes source of truth; device table projections
          added to acquire/extend/release; expired ACTIVE leases recycled.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.models.device_lease import DeviceLease
from backend.models.enums import LeaseStatus, LeaseType
from backend.models.host import Device

logger = logging.getLogger(__name__)

_DEFAULT_LEASE_SECONDS = 600


class LeaseProjectionError(RuntimeError):
    """Raised when device-table projection fails — indicates data inconsistency
    that should NOT be silently swallowed by claim loops."""
    pass


async def acquire_lease(
    db: AsyncSession,
    device_id: int,
    host_id: str,
    lease_type: LeaseType,
    agent_instance_id: str,
    job_id: int | None = None,
    reason: str | None = None,
    holder: str | None = None,
    lease_seconds: int = _DEFAULT_LEASE_SECONDS,
) -> DeviceLease | None:
    """Acquire a device lease with fencing-token semantics (ADR-0019 Phase 1).

    Within a savepoint:
      1. Read the current lease_generation snapshot from device.
      2. UPDATE device SET lease_generation = lease_generation + 1 RETURNING ...
      2.5. Recycle expired ACTIVE leases for this device → EXPIRED (Phase 2c).
      3. SELECT ... FOR UPDATE to check for a remaining ACTIVE lease.
      4. INSERT the new lease row with the incremented generation.
      5. Project to device table (status='BUSY', lock_run_id, lock_expires_at).

    If the partial-unique-index on (device_id WHERE status='ACTIVE')
    triggers an IntegrityError, the savepoint is rolled back and None
    is returned — the caller's outer transaction is NOT poisoned.

    Caller MUST be inside an active transaction (e.g. a FastAPI route
    handler with ``db: AsyncSession = Depends(get_async_db)``).

    Returns the created DeviceLease, or None if the device already has
    an active lease (conflict) or the device does not exist.

    Raises ValueError if the lease_type / required-fields contract is
    violated (JOB/SCRIPT requires job_id, MAINTENANCE requires reason+holder).
    """
    # Validate required fields per lease_type (ADR-0019 contract)
    if lease_type in (LeaseType.JOB, LeaseType.SCRIPT):
        if job_id is None:
            raise ValueError(
                f"lease_type={lease_type.value} requires job_id"
            )
    elif lease_type == LeaseType.MAINTENANCE:
        if not reason or not holder:
            raise ValueError(
                "lease_type=MAINTENANCE requires reason and holder"
            )

    device = await db.get(Device, device_id)
    if device is None:
        logger.warning("lease_acquire_device_not_found device=%s", device_id)
        return None

    # Step ①: snapshot current generation
    old_gen = device.lease_generation

    # Steps ②-⑥ inside a savepoint so a concurrent insert conflict
    # doesn't poison the outer transaction.
    try:
        async with db.begin_nested():
            # Step ②: atomically increment lease_generation on device
            result = await db.execute(
                update(Device)
                .where(Device.id == device_id)
                .values(lease_generation=Device.lease_generation + 1)
                .returning(Device.lease_generation)
            )
            row = result.fetchone()
            new_gen: int = row[0] if row is not None else old_gen + 1

            now = datetime.now(timezone.utc)
            expires_at = now + timedelta(seconds=lease_seconds)

            # Step ②.5: recycle expired ACTIVE leases for this device (Phase 2c)
            # Prevents expired leases from blocking new claims when lock_map is removed.
            await db.execute(
                update(DeviceLease)
                .where(
                    DeviceLease.device_id == device_id,
                    DeviceLease.status == LeaseStatus.ACTIVE.value,
                    DeviceLease.expires_at < now,
                )
                .values(status=LeaseStatus.EXPIRED.value)
            )

            # Step ③: check for remaining ACTIVE lease (FOR UPDATE
            # protects against concurrent claim of the same device)
            existing = await db.execute(
                select(DeviceLease)
                .where(
                    DeviceLease.device_id == device_id,
                    DeviceLease.status == LeaseStatus.ACTIVE.value,
                )
                .with_for_update()
            )
            if existing.scalar_one_or_none() is not None:
                logger.debug(
                    "lease_acquire_conflict device=%s active_exists=true", device_id,
                )
                raise _LeaseConflict()

            # Step ⑤: insert
            lease = DeviceLease(
                device_id=device_id,
                job_id=job_id,
                host_id=host_id,
                lease_type=lease_type.value,
                status=LeaseStatus.ACTIVE.value,
                fencing_token=f"{device_id}:{new_gen}",
                lease_generation=new_gen,
                agent_instance_id=agent_instance_id,
                reason=reason,
                holder=holder,
                acquired_at=now,
                renewed_at=now,
                expires_at=expires_at,
            )
            db.add(lease)
            await db.flush()  # triggers the partial unique index check

            # Step ⑥: project to device table (Phase 2c — transactional, not best-effort)
            if lease_type in (LeaseType.JOB, LeaseType.SCRIPT):
                proj_result = await db.execute(
                    update(Device)
                    .where(Device.id == device_id)
                    .values(
                        status="BUSY",
                        lock_run_id=job_id,
                        lock_expires_at=expires_at,
                    )
                )
                if proj_result.rowcount != 1:
                    raise LeaseProjectionError(
                        f"acquire_lease projection failed: device={device_id} "
                        f"rowcount={proj_result.rowcount}"
                    )

    except _LeaseConflict:
        logger.debug("lease_acquire_aborted device=%s reason=conflict", device_id)
        return None
    except IntegrityError:
        # Another concurrent acquire won the partial-unique-index race.
        # The savepoint is already rolled back.
        logger.debug("lease_acquire_aborted device=%s reason=integrity_error", device_id)
        return None
    # NOTE: LeaseProjectionError is NOT caught here — it must propagate
    # upward so callers treat data inconsistency as a hard error (500),
    # never silently swallowed as a normal claim conflict.

    logger.info(
        "lease_acquired device=%s lease=%s type=%s gen=%s token=%s",
        device_id, lease.id, lease_type.value, new_gen, lease.fencing_token,
    )
    return lease


async def extend_lease(
    db: AsyncSession,
    device_id: int,
    job_id: int,
    lease_type: LeaseType = LeaseType.JOB,
    ttl: int = _DEFAULT_LEASE_SECONDS,
) -> bool:
    """Extend expires_at for an ACTIVE lease (ADR-0019 Phase 2a).

    Only touches rows matching device_id + job_id + lease_type +
    status='ACTIVE'.  Returns True if a lease was extended.

    Phase 2c: lease update + device projection wrapped in a savepoint
    so projection failure rolls back the lease update.
    """
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=ttl)

    async with db.begin_nested():
        result = await db.execute(
            update(DeviceLease)
            .where(
                DeviceLease.device_id == device_id,
                DeviceLease.job_id == job_id,
                DeviceLease.lease_type == lease_type.value,
                DeviceLease.status == LeaseStatus.ACTIVE.value,
            )
            .values(renewed_at=now, expires_at=expires_at)
        )
        if not result.rowcount:
            return False

        # Phase 2c: project to device table
        proj_result = await db.execute(
            update(Device)
            .where(Device.id == device_id, Device.lock_run_id == job_id)
            .values(lock_expires_at=expires_at)
        )
        if proj_result.rowcount != 1:
            raise LeaseProjectionError(
                f"extend_lease projection failed: device={device_id} job={job_id}"
            )

    if result.rowcount:
        logger.info(
            "lease_extended device=%s job=%s type=%s expires=%s",
            device_id, job_id, lease_type.value, expires_at,
        )
    return bool(result.rowcount)


async def release_lease(
    db: AsyncSession,
    device_id: int,
    job_id: int,
    lease_type: LeaseType = LeaseType.JOB,
) -> bool:
    """Release an ACTIVE lease for a device+job (ADR-0019 Phase 2a).

    Only touches rows matching device_id + job_id + lease_type +
    status='ACTIVE'.  Returns True if a lease was released.

    Phase 2c: lease update + device projection wrapped in a savepoint
    so projection failure rolls back the lease update.
    """
    now = datetime.now(timezone.utc)

    async with db.begin_nested():
        result = await db.execute(
            update(DeviceLease)
            .where(
                DeviceLease.device_id == device_id,
                DeviceLease.job_id == job_id,
                DeviceLease.lease_type == lease_type.value,
                DeviceLease.status == LeaseStatus.ACTIVE.value,
            )
            .values(status=LeaseStatus.RELEASED.value, released_at=now)
        )
        if not result.rowcount:
            return False

        # Phase 2c: project to device table
        proj_result = await db.execute(
            update(Device)
            .where(Device.id == device_id, Device.lock_run_id == job_id)
            .values(
                status=case(
                    (Device.status == "BUSY", "ONLINE"),
                    else_=Device.status,
                ),
                lock_run_id=None,
                lock_expires_at=None,
            )
        )
        if proj_result.rowcount != 1:
            raise LeaseProjectionError(
                f"release_lease projection failed: device={device_id} job={job_id}"
            )

    if result.rowcount:
        logger.info(
            "lease_released device=%s job=%s type=%s",
            device_id, job_id, lease_type.value,
        )
    return bool(result.rowcount)


def release_lease_sync(
    db: Session,
    device_id: int,
    job_id: int,
    lease_type: LeaseType = LeaseType.JOB,
) -> bool:
    """Release an ACTIVE lease synchronously (ADR-0019 Phase 2b).

    Used by the recycler which runs in APScheduler threads (non-async).

    Phase 2c: lease update + device projection wrapped in a savepoint
    so projection failure rolls back the lease update.
    """
    now = datetime.now(timezone.utc)

    with db.begin_nested():
        result = db.execute(
            update(DeviceLease)
            .where(
                DeviceLease.device_id == device_id,
                DeviceLease.job_id == job_id,
                DeviceLease.lease_type == lease_type.value,
                DeviceLease.status == LeaseStatus.ACTIVE.value,
            )
            .values(status=LeaseStatus.RELEASED.value, released_at=now)
        )
        if not result.rowcount:
            return False

        # Phase 2c: project to device table
        proj_result = db.execute(
            update(Device)
            .where(Device.id == device_id, Device.lock_run_id == job_id)
            .values(
                status=case(
                    (Device.status == "BUSY", "ONLINE"),
                    else_=Device.status,
                ),
                lock_run_id=None,
                lock_expires_at=None,
            )
        )
        if proj_result.rowcount != 1:
            raise LeaseProjectionError(
                f"release_lease_sync projection failed: device={device_id} job={job_id}"
            )

    if result.rowcount:
        logger.info(
            "lease_released_sync device=%s job=%s type=%s",
            device_id, job_id, lease_type.value,
        )
    return bool(result.rowcount)


class _LeaseConflict(Exception):
    """Internal sentinel raised inside a savepoint when an ACTIVE lease
    already exists for the device."""
    pass
