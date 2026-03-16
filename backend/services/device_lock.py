"""Unified device-lock service.

Provides atomic acquire / extend / release for device lease management.
Both async (for FastAPI routes, workflow dispatcher) and sync (for legacy
scheduler/recycler) variants are exposed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_DEFAULT_LEASE_SECONDS = 600


# ── Async API (workflow dispatcher, agent_api) ────────────────────────────────

async def acquire_lock(
    db: AsyncSession,
    device_id: int,
    job_id: int,
    lease_seconds: int = _DEFAULT_LEASE_SECONDS,
) -> bool:
    """Atomically acquire a device lock for *job_id*.

    Succeeds when the device is free, the existing lease has expired, or the
    lock is already held by the same job (idempotent re-acquire / extend).

    Returns ``True`` if the lock was acquired, ``False`` otherwise.
    """
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=lease_seconds)
    result = await db.execute(
        text(
            """
            UPDATE device
            SET status       = 'BUSY',
                lock_run_id  = :job_id,
                lock_expires_at = :expires_at
            WHERE id = :device_id
              AND (lock_run_id IS NULL
                   OR lock_expires_at IS NULL
                   OR lock_expires_at < :now
                   OR lock_run_id = :job_id)
            """
        ),
        {
            "device_id": device_id,
            "job_id": job_id,
            "expires_at": expires_at,
            "now": now,
        },
    )
    acquired = result.rowcount == 1
    if acquired:
        logger.debug("lock_acquired device=%s job=%s expires=%s", device_id, job_id, expires_at)
    else:
        logger.debug("lock_acquire_failed device=%s job=%s", device_id, job_id)
    return acquired


async def extend_lock(
    db: AsyncSession,
    device_id: int,
    job_id: int,
    lease_seconds: int = _DEFAULT_LEASE_SECONDS,
) -> bool:
    """Extend the lease for an existing lock held by *job_id*.

    Returns ``True`` if the lock was extended, ``False`` if the device is
    unlocked or held by a different job.
    """
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=lease_seconds)
    result = await db.execute(
        text(
            """
            UPDATE device
            SET lock_expires_at = :expires_at
            WHERE id = :device_id
              AND lock_run_id = :job_id
            """
        ),
        {
            "device_id": device_id,
            "job_id": job_id,
            "expires_at": expires_at,
        },
    )
    return result.rowcount == 1


async def release_lock(
    db: AsyncSession,
    device_id: int,
    job_id: int,
) -> bool:
    """Release the device lock held by *job_id*.

    Only clears the lock if ``lock_run_id == job_id``, preventing a job from
    releasing another job's lock.  Restores status to ``ONLINE`` if it was
    ``BUSY``.

    Returns ``True`` if the lock was released, ``False`` otherwise.
    """
    result = await db.execute(
        text(
            """
            UPDATE device
            SET status = CASE WHEN status = 'BUSY' THEN 'ONLINE' ELSE status END,
                lock_run_id     = NULL,
                lock_expires_at = NULL
            WHERE id = :device_id
              AND lock_run_id = :job_id
            """
        ),
        {
            "device_id": device_id,
            "job_id": job_id,
        },
    )
    released = result.rowcount == 1
    if released:
        logger.debug("lock_released device=%s job=%s", device_id, job_id)
    return released


# ── Sync API (legacy scheduler/recycler) ──────────────────────────────────────

def acquire_lock_sync(
    db: Session,
    device_id: int,
    job_id: int,
    lease_seconds: int = _DEFAULT_LEASE_SECONDS,
) -> bool:
    """Synchronous variant of :func:`acquire_lock`."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=lease_seconds)
    result = db.execute(
        text(
            """
            UPDATE device
            SET status       = 'BUSY',
                lock_run_id  = :job_id,
                lock_expires_at = :expires_at
            WHERE id = :device_id
              AND (lock_run_id IS NULL
                   OR lock_expires_at IS NULL
                   OR lock_expires_at < :now
                   OR lock_run_id = :job_id)
            """
        ),
        {
            "device_id": device_id,
            "job_id": job_id,
            "expires_at": expires_at,
            "now": now,
        },
    )
    return result.rowcount == 1


def extend_lock_sync(
    db: Session,
    device_id: int,
    job_id: int,
    lease_seconds: int = _DEFAULT_LEASE_SECONDS,
) -> bool:
    """Synchronous variant of :func:`extend_lock`."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=lease_seconds)
    result = db.execute(
        text(
            """
            UPDATE device
            SET lock_expires_at = :expires_at
            WHERE id = :device_id
              AND lock_run_id = :job_id
            """
        ),
        {
            "device_id": device_id,
            "job_id": job_id,
            "expires_at": expires_at,
        },
    )
    return result.rowcount == 1


def release_lock_sync(
    db: Session,
    device_id: int,
    job_id: int,
) -> bool:
    """Synchronous variant of :func:`release_lock`."""
    result = db.execute(
        text(
            """
            UPDATE device
            SET status = CASE WHEN status = 'BUSY' THEN 'ONLINE' ELSE status END,
                lock_run_id     = NULL,
                lock_expires_at = NULL
            WHERE id = :device_id
              AND lock_run_id = :job_id
            """
        ),
        {
            "device_id": device_id,
            "job_id": job_id,
        },
    )
    return result.rowcount == 1
