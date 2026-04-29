"""Device Lease Manager — ADR-0019 Phase 1.

Provides atomic acquire/release for device leases with full fencing-token
semantics.  Each successful acquire increments device.lease_generation and
records a snapshot of the new generation in device_leases.

Phase 1: standalone module — does NOT replace device_lock.py or modify
          the claim_jobs flow in agent_api.py.
Phase 2+: integrated into claim with FOR UPDATE SKIP LOCKED patterns.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.device_lease import DeviceLease
from backend.models.enums import LeaseStatus, LeaseType
from backend.models.host import Device

logger = logging.getLogger(__name__)

_DEFAULT_LEASE_SECONDS = 600


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
      3. SELECT ... FOR UPDATE to check for an existing ACTIVE lease.
      4. INSERT the new lease row with the incremented generation.

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

    # Steps ②-⑤ inside a savepoint so a concurrent insert conflict
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

            # Step ③: check for existing ACTIVE lease (FOR UPDATE
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
                # Roll back savepoint and signal conflict
                raise _LeaseConflict()

            now = datetime.now(timezone.utc)
            expires_at = now + timedelta(seconds=lease_seconds)

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

    except _LeaseConflict:
        logger.debug("lease_acquire_aborted device=%s reason=conflict", device_id)
        return None
    except IntegrityError:
        # Another concurrent acquire won the partial-unique-index race.
        # The savepoint is already rolled back.
        logger.debug("lease_acquire_aborted device=%s reason=integrity_error", device_id)
        return None

    logger.info(
        "lease_acquired device=%s lease=%s type=%s gen=%s token=%s",
        device_id, lease.id, lease_type.value, new_gen, lease.fencing_token,
    )
    return lease


async def release_lease(
    db: AsyncSession,
    device_id: int,
    lease_type: LeaseType | None = None,
) -> bool:
    """Release all ACTIVE leases for a device (ADR-0019 Phase 1).

    Optionally filter by lease_type.  Sets status=RELEASED and
    released_at to the current time.

    Returns True if at least one lease was released.
    """
    now = datetime.now(timezone.utc)
    conditions = [
        DeviceLease.device_id == device_id,
        DeviceLease.status == LeaseStatus.ACTIVE.value,
    ]
    if lease_type is not None:
        conditions.append(DeviceLease.lease_type == lease_type.value)

    result = await db.execute(
        update(DeviceLease)
        .where(*conditions)
        .values(status=LeaseStatus.RELEASED.value, released_at=now)
    )
    released = result.rowcount
    if released:
        logger.info("lease_released device=%s count=%s", device_id, released)
    return bool(released)


class _LeaseConflict(Exception):
    """Internal sentinel raised inside a savepoint when an ACTIVE lease
    already exists for the device."""
    pass
