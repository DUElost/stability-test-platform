"""Periodic reaper for expired manual network-supply sessions (ADR-0066 D2.6 / B13 U2).

Transitions expired ``source='manual'`` sessions (``PROVISIONING`` / ``ACTIVE``
with ``expires_at <= now``) to ``RELEASING`` after verifying the bound
``manual_network_supply`` lease generation matches. Never releases the lease or
marks ``RELEASED`` before Agent confirmation or dedicated admin audit-override.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.database import SessionLocal
from backend.models.device_lease import DeviceLease
from backend.models.enums import LeaseStatus, LeaseType
from backend.models.host import Device
from backend.models.network_session import DeviceNetworkSession
from backend.services.network_return import MANUAL_NETWORK_LEASE_REASON

logger = logging.getLogger(__name__)

_DEFAULT_BATCH_LIMIT = 100


def reap_expired_manual_network_sessions_sync(
    db: Session,
    *,
    now: datetime | None = None,
    batch_limit: int = _DEFAULT_BATCH_LIMIT,
) -> dict[str, int]:
    """Transition expired manual sessions to ``RELEASING`` in deterministic lock order."""
    ts = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    # Explicit total order (device_id ASC, session_id ASC) before any FOR UPDATE.
    candidates = list(
        db.execute(
            select(DeviceNetworkSession.device_id, DeviceNetworkSession.id)
            .where(
                DeviceNetworkSession.source == "manual",
                DeviceNetworkSession.status.in_(("PROVISIONING", "ACTIVE")),
                DeviceNetworkSession.expires_at.is_not(None),
                DeviceNetworkSession.expires_at <= ts,
            )
            .order_by(DeviceNetworkSession.device_id.asc(), DeviceNetworkSession.id.asc())
            .limit(batch_limit)
        ).all()
    )

    transitioned = 0
    skipped = 0
    for device_id, session_id in candidates:
        device = (
            db.execute(
                select(Device)
                .where(Device.id == device_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalars().first()
        if device is None:
            skipped += 1
            continue

        session = (
            db.execute(
                select(DeviceNetworkSession)
                .where(DeviceNetworkSession.id == session_id)
                .with_for_update()
            )
        ).scalars().first()
        if (
            session is None
            or session.source != "manual"
            or session.status not in ("PROVISIONING", "ACTIVE")
            or session.expires_at is None
        ):
            skipped += 1
            continue

        exp = (
            session.expires_at.replace(tzinfo=timezone.utc)
            if session.expires_at.tzinfo is None
            else session.expires_at.astimezone(timezone.utc)
        )
        if exp > ts:
            skipped += 1
            continue

        lease = (
            db.execute(
                select(DeviceLease)
                .where(
                    DeviceLease.device_id == device_id,
                    DeviceLease.status == LeaseStatus.ACTIVE.value,
                )
                .with_for_update()
            )
        ).scalars().first()
        if (
            lease is None
            or lease.id != session.lease_id
            or lease.lease_type != LeaseType.MAINTENANCE.value
            or lease.reason != MANUAL_NETWORK_LEASE_REASON
            or lease.lease_generation != session.generation
        ):
            skipped += 1
            continue

        session.status = "RELEASING"
        session.updated_at = ts
        transitioned += 1

    if transitioned:
        db.flush()
    return {"checked": len(candidates), "transitioned": transitioned, "skipped": skipped}


def network_session_reaper_job() -> dict[str, int]:
    """APScheduler singleton entry point (sync, threadpool executor)."""
    session = SessionLocal()
    try:
        stats = reap_expired_manual_network_sessions_sync(session)
        session.commit()
        if stats["transitioned"]:
            logger.info(
                "network_session_reaper_transitioned checked=%d transitioned=%d skipped=%d",
                stats["checked"],
                stats["transitioned"],
                stats["skipped"],
            )
        return stats
    except Exception:
        session.rollback()
        logger.exception("network_session_reaper_failed")
        raise
    finally:
        session.close()
