"""Unified network-supply return, manual release & audit-override service
(ADR-0066 D2.6, D3.4, D4.4–D4.5 / B13 U2).

Strictly acyclic leaf service: depends only on ORM models, shared contracts,
``audit_writer``, and ``errors`` (no imports from ``device_return`` or
``network_supply``) so C5 service acyclicity and inner-import ratchets hold.

Locking order for all mutating operations aligns with ADR-0061 / ADR-0066:
    ``Device FOR UPDATE`` → ``DeviceNetworkSession FOR UPDATE`` → ``DeviceLease FOR UPDATE``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent.contracts.device_return import (
    DEBT_CLOSE_OVERRIDDEN,
    DEBT_STATE_CLOSED,
    DEBT_STATE_OPEN,
    DISARM_RESULT_CONFIRMED,
    DISARM_RESULT_FAILED,
    FAMILY_RETURN_DEBT,
    FAMILY_RETURN_RETURNED,
    FAMILY_RETURN_UNVERIFIED,
    RETURN_SOURCE_DISCHARGE,
    RETURN_SOURCE_JOB,
    RETURN_SOURCE_OVERRIDE,
    RETURN_SOURCE_STOCK,
    probe_result_policy,
)
from backend.models.device_lease import DeviceLease
from backend.models.device_return_debt import DeviceReturnDebt
from backend.models.enums import LeaseStatus, LeaseType
from backend.models.host import Device
from backend.models.job import JobInstance
from backend.models.network_session import (
    ACTIVE_NETWORK_SESSION_STATUSES,
    DeviceNetworkSession,
)
from backend.models.resource_pool import ResourcePool
from backend.services.audit_writer import record_audit
from backend.services.errors import (
    BadRequest,
    Conflict,
    NotFound,
    UnprocessableEntity,
)

logger = logging.getLogger(__name__)

# Shared lease markers (kept in sync with device_return.py & network_supply.py).
DEBT_LEASE_REASON = "归还欠账"
DEBT_LEASE_HOLDER = "platform"
DEBT_LEASE_AGENT_INSTANCE_ID = "platform"
_DEBT_LEASE_EXPIRES_AT = datetime(9999, 1, 1, tzinfo=timezone.utc)

MANUAL_NETWORK_LEASE_REASON = "manual_network_supply"
MANUAL_NETWORK_RETURN_SOURCE_KIND = "manual_network_session"

# Revoke directives remain eligible for heartbeat delivery longer than the max
# usbnet_grant TTL (1800s) so an Agent reconnecting after override always
# executes ``stp-agent-priv usbnet-revoke`` before any old grant could survive.
_USBNET_REVOKE_WINDOW_SECONDS = 2100

_USBNET_REVOKE_PENDING_KEY = "_usbnet_revoke_pending"
_USBNET_REVOKE_REQUESTED_AT_KEY = "_usbnet_revoke_requested_at"
_USBNET_REVOKE_ACKED_AT_KEY = "_usbnet_revoke_acked_at"


def _now(ts: datetime | None = None) -> datetime:
    if ts is None:
        return datetime.now(timezone.utc)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _is_debt_lease(lease: DeviceLease | None) -> bool:
    if lease is None:
        return False
    return (
        lease.lease_type == LeaseType.MAINTENANCE.value
        and lease.reason == DEBT_LEASE_REASON
        and lease.holder == DEBT_LEASE_HOLDER
    )


def _is_manual_network_lease(lease: DeviceLease | None) -> bool:
    if lease is None:
        return False
    return (
        lease.lease_type == LeaseType.MAINTENANCE.value
        and lease.reason == MANUAL_NETWORK_LEASE_REASON
    )


def _parse_fencing_generation(device_id: int, fencing_token: str | None) -> Optional[int]:
    if not isinstance(fencing_token, str) or ":" not in fencing_token:
        return None
    dev_part, gen_part = fencing_token.split(":", 1)
    try:
        if int(dev_part) != int(device_id):
            return None
        gen = int(gen_part)
    except ValueError:
        return None
    return gen if gen >= 1 else None


def _lock_device_sync(db: Session, device_id: int) -> Device | None:
    db.flush()
    return (
        db.execute(
            select(Device)
            .where(Device.id == device_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalars().first()


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


def _open_debts_sync(db: Session, device_id: int) -> list[DeviceReturnDebt]:
    return list(
        db.execute(
            select(DeviceReturnDebt).where(
                DeviceReturnDebt.device_id == device_id,
                DeviceReturnDebt.state == DEBT_STATE_OPEN,
            )
        ).scalars().all()
    )


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


def _convert_lease_to_debt_sync(
    db: Session,
    device: Device,
    lease: DeviceLease,
    *,
    now: datetime,
) -> DeviceLease:
    """In-place conversion (manual_network_supply → ADR-0061 debt lease, N → N+1)."""
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


def _recompute_return_state_from_open_debts(
    device: Device,
    open_rows: list[DeviceReturnDebt],
    *,
    now: datetime,
) -> None:
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
                if row.source_kind == "stock"
                else RETURN_SOURCE_JOB
            )
        current[family] = entry
    device.return_state = current


def _mark_usbnet_revoke_pending(
    session: DeviceNetworkSession,
    *,
    now: datetime,
) -> None:
    snap = dict(session.prior_network_snapshot or {})
    snap[_USBNET_REVOKE_PENDING_KEY] = True
    snap[_USBNET_REVOKE_REQUESTED_AT_KEY] = now.isoformat()
    snap.pop(_USBNET_REVOKE_ACKED_AT_KEY, None)
    session.prior_network_snapshot = snap
    flag_modified(session, "prior_network_snapshot")


def _serialize_session(
    session: DeviceNetworkSession,
    *,
    device_serial: str | None = None,
    pool_name: str | None = None,
) -> dict[str, Any]:
    return {
        "id": session.id,
        "device_id": session.device_id,
        "device_serial": device_serial,
        "host_id": session.host_id,
        "resource_pool_id": session.resource_pool_id,
        "pool_name": pool_name,
        "resource_type": session.resource_type,
        "egress": session.egress,
        "source": session.source,
        "plan_run_id": session.plan_run_id,
        "job_instance_id": session.job_instance_id,
        "lease_id": session.lease_id,
        "generation": session.generation,
        "legacy_path": bool(session.legacy_path),
        "status": session.status,
        "release_reason": session.release_reason,
        "assigned_ip": session.assigned_ip,
        "usb_devpath": session.usb_devpath,
        "created_by": session.created_by,
        "expires_at": _as_utc(session.expires_at).isoformat() if session.expires_at else None,
        "created_at": _as_utc(session.created_at).isoformat() if session.created_at else None,
        "updated_at": _as_utc(session.updated_at).isoformat() if session.updated_at else None,
        "released_at": _as_utc(session.released_at).isoformat() if session.released_at else None,
    }


# ── Hooks called inside device_return.py transactions ────────────────────────


def job_usbnet_footprint_decision_sync(
    db: Session,
    job: JobInstance,
) -> tuple[bool, bool]:
    """Return ``(force_include_usbnet, allow_usbnet)`` for ``job``.

    - ``force_include_usbnet``: True when the job has a bound (non-``RESERVED``)
      unreleased ``usb_net`` session, so even a crash before ``StepTrace`` was
      written still establishes a ``usb_net`` footprint (ADR-0066 D4.3–D4.4).
    - ``allow_usbnet``: False when the job's session is ``wifi`` (or absent with
      no explicit ``usb_net`` disarm report), preventing ``setup_network`` used
      for ``wifi`` from spuriously creating ``usb_net`` footprints or debts.
    """
    rows = list(
        db.execute(
            select(DeviceNetworkSession.resource_type, DeviceNetworkSession.status)
            .where(
                DeviceNetworkSession.job_instance_id == job.id,
                DeviceNetworkSession.source == "job",
            )
        ).all()
    )
    if rows:
        has_usbnet = any(rt == "usb_net" for rt, _ in rows)
        has_bound_usbnet = any(
            rt == "usb_net" and st in ("PROVISIONING", "ACTIVE", "RELEASING", "RELEASE_FAILED")
            for rt, st in rows
        )
        return has_bound_usbnet, has_usbnet
    raw_disarm = getattr(job, "disarm_results", None)
    if isinstance(raw_disarm, Mapping) and "usb_net" in raw_disarm:
        return False, True
    return False, False


async def job_usbnet_footprint_decision_async(
    db: AsyncSession,
    job: JobInstance,
) -> tuple[bool, bool]:
    """Async counterpart to ``job_usbnet_footprint_decision_sync``."""
    rows = list(
        (
            await db.execute(
                select(DeviceNetworkSession.resource_type, DeviceNetworkSession.status)
                .where(
                    DeviceNetworkSession.job_instance_id == job.id,
                    DeviceNetworkSession.source == "job",
                )
            )
        ).all()
    )
    if rows:
        has_usbnet = any(rt == "usb_net" for rt, _ in rows)
        has_bound_usbnet = any(
            rt == "usb_net" and st in ("PROVISIONING", "ACTIVE", "RELEASING", "RELEASE_FAILED")
            for rt, st in rows
        )
        return has_bound_usbnet, has_usbnet
    raw_disarm = getattr(job, "disarm_results", None)
    if isinstance(raw_disarm, Mapping) and "usb_net" in raw_disarm:
        return False, True
    return False, False


def _settle_session_rows(
    sessions: list[DeviceNetworkSession],
    *,
    usbnet_confirmed: bool,
    now: datetime,
) -> int:
    settled = 0
    for sess in sessions:
        if (
            sess.status == "RESERVED"
            and sess.lease_id is None
            and sess.generation is None
        ):
            sess.status = "RELEASED"
            sess.release_reason = "unclaimed_canceled"
            sess.released_at = now
            sess.updated_at = now
            settled += 1
            continue
        if sess.resource_type == "wifi":
            sess.status = "RELEASED"
            sess.release_reason = "teardown_confirmed"
            sess.released_at = now
            sess.updated_at = now
            settled += 1
            continue
        if sess.resource_type == "usb_net":
            if usbnet_confirmed:
                sess.status = "RELEASED"
                sess.release_reason = "teardown_confirmed"
                sess.released_at = now
                sess.updated_at = now
                settled += 1
            else:
                sess.status = "RELEASE_FAILED"
                sess.updated_at = now
    return settled


def settle_job_network_session_sync(
    db: Session,
    *,
    device_id: int,
    job_id: int,
    results: Mapping[str, Mapping[str, Any]],
    now: datetime,
) -> int:
    """Settle job ``device_network_session`` inside ``handle_job_lease_release_sync``.

    - ``RESERVED`` (unclaimed) → ``RELEASED(unclaimed_canceled)``;
    - ``wifi`` → ``RELEASED(teardown_confirmed)``;
    - ``usb_net`` with ``confirmed`` → ``RELEASED(teardown_confirmed)`` (even if
      another family on the same device keeps open debt and converts the lease);
    - ``usb_net`` without ``confirmed`` → ``RELEASE_FAILED`` (stays unreleased
      and counted in quota until discharged, late-confirmed, or overridden).
    """
    sessions = list(
        db.execute(
            select(DeviceNetworkSession)
            .where(
                DeviceNetworkSession.device_id == device_id,
                DeviceNetworkSession.job_instance_id == job_id,
                DeviceNetworkSession.source == "job",
                DeviceNetworkSession.status.in_(ACTIVE_NETWORK_SESSION_STATUSES),
            )
            .order_by(DeviceNetworkSession.id.asc())
            .with_for_update()
        ).scalars().all()
    )
    if not sessions:
        return 0
    usbnet_entry = results.get("usb_net") if isinstance(results, Mapping) else None
    usbnet_confirmed = (
        isinstance(usbnet_entry, Mapping)
        and str(usbnet_entry.get("result") or "") == DISARM_RESULT_CONFIRMED
    )
    count = _settle_session_rows(sessions, usbnet_confirmed=usbnet_confirmed, now=now)
    db.flush()
    return count


async def settle_job_network_session_async(
    db: AsyncSession,
    *,
    device_id: int,
    job_id: int,
    results: Mapping[str, Mapping[str, Any]],
    now: datetime,
) -> int:
    """Async counterpart to ``settle_job_network_session_sync``."""
    sessions = list(
        (
            await db.execute(
                select(DeviceNetworkSession)
                .where(
                    DeviceNetworkSession.device_id == device_id,
                    DeviceNetworkSession.job_instance_id == job_id,
                    DeviceNetworkSession.source == "job",
                    DeviceNetworkSession.status.in_(ACTIVE_NETWORK_SESSION_STATUSES),
                )
                .order_by(DeviceNetworkSession.id.asc())
                .with_for_update()
            )
        ).scalars().all()
    )
    if not sessions:
        return 0
    usbnet_entry = results.get("usb_net") if isinstance(results, Mapping) else None
    usbnet_confirmed = (
        isinstance(usbnet_entry, Mapping)
        and str(usbnet_entry.get("result") or "") == DISARM_RESULT_CONFIRMED
    )
    count = _settle_session_rows(sessions, usbnet_confirmed=usbnet_confirmed, now=now)
    await db.flush()
    return count


def close_usbnet_sessions_on_discharge_sync(
    db: Session,
    *,
    device_id: int,
    now: datetime,
) -> int:
    """Close unreleased bound ``usb_net`` sessions atomically inside ``apply_discharge_sync``."""
    sessions = list(
        db.execute(
            select(DeviceNetworkSession)
            .where(
                DeviceNetworkSession.device_id == device_id,
                DeviceNetworkSession.resource_type == "usb_net",
                DeviceNetworkSession.lease_id.is_not(None),
                DeviceNetworkSession.generation.is_not(None),
                DeviceNetworkSession.status.in_(ACTIVE_NETWORK_SESSION_STATUSES),
            )
            .order_by(DeviceNetworkSession.id.asc())
            .with_for_update()
        ).scalars().all()
    )
    for sess in sessions:
        sess.status = "RELEASED"
        sess.release_reason = "debt_discharged"
        sess.released_at = now
        sess.updated_at = now
    if sessions:
        db.flush()
    return len(sessions)


def close_usbnet_sessions_on_override_sync(
    db: Session,
    *,
    device: Device,
    now: datetime,
) -> dict[str, Any]:
    """Close unreleased bound ``usb_net`` sessions inside generic ``apply_override_sync``.

    Mirrors ``audit_override_network_session_sync`` for unconverted manual ``usb_net``
    sessions whose bound ``manual_network_supply`` lease has not yet been converted to
    an ADR-0061 debt lease: releases that lease when zero open debts remain across all
    families, or converts it in-place to a standard debt lease (``N → N+1``) when
    another family still holds open debt. Never touches leases bound to non-``usb_net``
    (e.g. manual ``wifi``) sessions.
    """
    sessions = list(
        db.execute(
            select(DeviceNetworkSession)
            .where(
                DeviceNetworkSession.device_id == device.id,
                DeviceNetworkSession.resource_type == "usb_net",
                DeviceNetworkSession.lease_id.is_not(None),
                DeviceNetworkSession.generation.is_not(None),
                DeviceNetworkSession.status.in_(ACTIVE_NETWORK_SESSION_STATUSES),
            )
            .order_by(DeviceNetworkSession.id.asc())
            .with_for_update()
        ).scalars().all()
    )
    closed_lease_ids: set[int] = set()
    for sess in sessions:
        if sess.lease_id is not None:
            closed_lease_ids.add(int(sess.lease_id))
        _mark_usbnet_revoke_pending(sess, now=now)
        sess.status = "RELEASED"
        sess.release_reason = "manual_override"
        sess.released_at = now
        sess.updated_at = now
    if sessions:
        db.flush()

    manual_lease_released = False
    manual_lease_converted_to_debt = False
    if closed_lease_ids:
        lease = _active_lease_sync(db, device.id)
        if (
            lease is not None
            and _is_manual_network_lease(lease)
            and int(lease.id) in closed_lease_ids
        ):
            remaining_debts = _open_debts_sync(db, device.id)
            if not remaining_debts:
                lease.status = LeaseStatus.RELEASED.value
                lease.released_at = now
                db.flush()
                manual_lease_released = True
            else:
                _convert_lease_to_debt_sync(db, device, lease, now=now)
                manual_lease_converted_to_debt = True

    return {
        "closed_sessions": len(sessions),
        "manual_lease_released": manual_lease_released,
        "manual_lease_converted_to_debt": manual_lease_converted_to_debt,
    }


def close_usbnet_session_on_late_confirmed_sync(
    db: Session,
    *,
    device_id: int,
    job_id: int,
    fencing_token: str,
    now: datetime,
) -> int:
    """Close matching ``(job_id, generation=N)`` ``usb_net`` session inside ``apply_late_confirmed_sync``."""
    orig_gen = _parse_fencing_generation(device_id, fencing_token)
    if orig_gen is None:
        return 0
    sessions = list(
        db.execute(
            select(DeviceNetworkSession)
            .where(
                DeviceNetworkSession.device_id == device_id,
                DeviceNetworkSession.source == "job",
                DeviceNetworkSession.job_instance_id == job_id,
                DeviceNetworkSession.resource_type == "usb_net",
                DeviceNetworkSession.generation == orig_gen,
                DeviceNetworkSession.lease_id.is_not(None),
                DeviceNetworkSession.status.in_(ACTIVE_NETWORK_SESSION_STATUSES),
            )
            .order_by(DeviceNetworkSession.id.asc())
            .with_for_update()
        ).scalars().all()
    )
    for sess in sessions:
        sess.status = "RELEASED"
        sess.release_reason = "late_confirmed"
        sess.released_at = now
        sess.updated_at = now
    if sessions:
        db.flush()
    return len(sessions)


async def close_usbnet_session_on_late_confirmed_async(
    db: AsyncSession,
    *,
    device_id: int,
    job_id: int,
    fencing_token: str,
    now: datetime,
) -> int:
    """Async counterpart to ``close_usbnet_session_on_late_confirmed_sync``."""
    orig_gen = _parse_fencing_generation(device_id, fencing_token)
    if orig_gen is None:
        return 0
    sessions = list(
        (
            await db.execute(
                select(DeviceNetworkSession)
                .where(
                    DeviceNetworkSession.device_id == device_id,
                    DeviceNetworkSession.source == "job",
                    DeviceNetworkSession.job_instance_id == job_id,
                    DeviceNetworkSession.resource_type == "usb_net",
                    DeviceNetworkSession.generation == orig_gen,
                    DeviceNetworkSession.lease_id.is_not(None),
                    DeviceNetworkSession.status.in_(ACTIVE_NETWORK_SESSION_STATUSES),
                )
                .order_by(DeviceNetworkSession.id.asc())
                .with_for_update()
            )
        ).scalars().all()
    )
    for sess in sessions:
        sess.status = "RELEASED"
        sess.release_reason = "late_confirmed"
        sess.released_at = now
        sess.updated_at = now
    if sessions:
        await db.flush()
    return len(sessions)


def enrich_open_debts_with_usbnet_sessions_sync(
    db: Session,
    debts_payload: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach bound ``usb_net`` session identity to heartbeat ``return_debts`` entries."""
    usbnet_device_ids = [
        int(item["device_id"])
        for item in debts_payload
        if isinstance(item.get("families"), list) and "usb_net" in item["families"]
    ]
    if not usbnet_device_ids:
        return debts_payload
    rows = list(
        db.execute(
            select(DeviceNetworkSession)
            .where(
                DeviceNetworkSession.device_id.in_(usbnet_device_ids),
                DeviceNetworkSession.resource_type == "usb_net",
                DeviceNetworkSession.lease_id.is_not(None),
                DeviceNetworkSession.generation.is_not(None),
                DeviceNetworkSession.status.in_(ACTIVE_NETWORK_SESSION_STATUSES),
            )
            .order_by(DeviceNetworkSession.id.asc())
        ).scalars().all()
    )
    by_dev: dict[int, list[dict[str, Any]]] = {}
    for sess in rows:
        by_dev.setdefault(sess.device_id, []).append(
            {
                "session_id": sess.id,
                "generation": sess.generation,
                "egress": sess.egress,
                "usb_devpath": sess.usb_devpath,
            }
        )
    for item in debts_payload:
        did = int(item["device_id"])
        if did in by_dev:
            item["usbnet_sessions"] = by_dev[did]
    return debts_payload


# ── Manual release, Agent callback & dedicated audit-override ────────────────


def request_manual_network_release_sync(
    db: Session,
    *,
    session_id: int,
    admin_username: str,
    admin_user_id: int | None = None,
    request: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Admin-initiated force release of a manual session (ADR-0066 D2.6).

    Transitions ``PROVISIONING`` / ``ACTIVE`` → ``RELEASING`` without releasing
    the bound ``DeviceLease`` or marking ``RELEASED`` before Agent confirmation.
    """
    ts = _now(now)
    pre_device_id = db.execute(
        select(DeviceNetworkSession.device_id).where(DeviceNetworkSession.id == session_id)
    ).scalar_one_or_none()
    if pre_device_id is None:
        raise NotFound({
            "code": "SESSION_NOT_FOUND",
            "message": f"network session {session_id} not found",
        })

    device = _lock_device_sync(db, int(pre_device_id))
    session = db.execute(
        select(DeviceNetworkSession)
        .where(DeviceNetworkSession.id == session_id)
        .with_for_update()
    ).scalar_one_or_none()
    if session is None:
        raise NotFound({
            "code": "SESSION_NOT_FOUND",
            "message": f"network session {session_id} not found",
        })
    if session.source != "manual":
        raise BadRequest({
            "code": "NOT_MANUAL_SESSION",
            "message": "only manual network sessions can be released via this endpoint",
        })

    if session.status in ("PROVISIONING", "ACTIVE"):
        prev_status = session.status
        session.status = "RELEASING"
        session.updated_at = ts
        record_audit(
            db,
            action="manual_network_supply_release_requested",
            resource_type="device_network_session",
            resource_id=session.id,
            details={
                "device_id": session.device_id,
                "host_id": session.host_id,
                "resource_type": session.resource_type,
                "generation": session.generation,
                "from_status": prev_status,
            },
            user_id=admin_user_id,
            username=admin_username,
            request=request,
        )
        db.commit()

    pool = db.get(ResourcePool, session.resource_pool_id)
    return _serialize_session(
        session,
        device_serial=device.serial if device else None,
        pool_name=pool.name if pool else None,
    )


def report_manual_network_release_sync(
    db: Session,
    *,
    session_id: int,
    host_id: str,
    generation: int,
    confirmed: bool,
    evidence: Mapping[str, Any] | None = None,
    error_message: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Agent callback for manual ``wifi`` / ``usb_net`` release (ADR-0066 D3.4 & D4.5).

    - Manual ``wifi`` ``confirmed=True`` → ``RELEASED(manual_released)`` + release
      ``manual_network_supply`` lease (or convert to debt lease if other families
      hold open debts). Never creates a ``DeviceReturnDebt`` row for ``wifi``.
    - Manual ``wifi`` ``confirmed=False`` → ``RELEASE_FAILED``, ZERO debt rows,
      keep ``manual_network_supply`` lease at the exact same ``generation`` so
      heartbeat retry can complete release once the device reconnects.
    - Manual ``usb_net`` ``confirmed=True`` (dual-side verified!) → ``RELEASED(manual_released)``
      + mark ``usb_net`` return state ``returned`` + release/convert lease.
    - Manual ``usb_net`` ``confirmed=False`` → ``RELEASE_FAILED`` + upsert
      ``DeviceReturnDebt(family="usb_net", source_kind="manual_network_session")``
      + convert ``manual_network_supply`` lease in-place to ADR-0061 debt lease
      (``generation N → N+1``) so ``ReturnDebtConsumer`` discharges with ``N+1``.
    """
    ts = _now(now)
    pre_device_id = db.execute(
        select(DeviceNetworkSession.device_id).where(DeviceNetworkSession.id == session_id)
    ).scalar_one_or_none()
    if pre_device_id is None:
        raise NotFound({
            "code": "SESSION_NOT_FOUND",
            "message": f"network session {session_id} not found",
        })

    device = _lock_device_sync(db, int(pre_device_id))
    if device is None:
        raise NotFound({"code": "DEVICE_NOT_FOUND", "message": f"device {pre_device_id} not found"})

    session = db.execute(
        select(DeviceNetworkSession)
        .where(DeviceNetworkSession.id == session_id)
        .with_for_update()
    ).scalar_one_or_none()
    if session is None:
        raise NotFound({
            "code": "SESSION_NOT_FOUND",
            "message": f"network session {session_id} not found",
        })
    if session.source != "manual":
        raise BadRequest({
            "code": "NOT_MANUAL_SESSION",
            "message": "release-report applies only to manual network sessions",
        })
    if session.host_id != host_id:
        raise Conflict({
            "code": "SESSION_HOST_MISMATCH",
            "message": f"session {session_id} belongs to host {session.host_id!r}",
        })
    if session.generation != generation:
        raise Conflict({
            "code": "SESSION_GENERATION_MISMATCH",
            "message": f"session {session_id} generation mismatch",
        })
    if session.status == "RELEASED":
        return {
            "session_id": session.id,
            "status": session.status,
            "release_reason": session.release_reason,
            "lease_released": False,
            "converted_to_debt": False,
            "fencing_token": None,
            "open_debt_count": len(_open_debts_sync(db, device.id)),
            "idempotent": True,
        }

    lease = _active_lease_sync(db, device.id)
    if (
        lease is None
        or not _is_manual_network_lease(lease)
        or lease.id != session.lease_id
        or lease.lease_generation != generation
    ):
        raise Conflict({
            "code": "MANUAL_LEASE_INVALID",
            "message": f"active manual_network_supply lease mismatch for session {session_id}",
        })

    lease_released = False
    converted_to_debt = False
    fencing_token: Optional[str] = lease.fencing_token

    if session.resource_type == "wifi":
        if confirmed:
            session.status = "RELEASED"
            session.release_reason = "manual_released"
            session.released_at = ts
            session.updated_at = ts
            open_rows = _open_debts_sync(db, device.id)
            if not open_rows:
                lease.status = LeaseStatus.RELEASED.value
                lease.released_at = ts
                lease_released = True
                fencing_token = None
            else:
                converted = _convert_lease_to_debt_sync(db, device, lease, now=ts)
                converted_to_debt = True
                fencing_token = converted.fencing_token
        else:
            session.status = "RELEASE_FAILED"
            session.updated_at = ts
            # Keep manual_network_supply lease ACTIVE at the exact same generation;
            # NO DeviceReturnDebt row created for wifi (ADR-0066 D3.4).
        db.commit()
        return {
            "session_id": session.id,
            "status": session.status,
            "release_reason": session.release_reason,
            "lease_released": lease_released,
            "converted_to_debt": converted_to_debt,
            "fencing_token": fencing_token,
            "open_debt_count": len(_open_debts_sync(db, device.id)),
            "idempotent": False,
        }

    # resource_type == "usb_net"
    if confirmed:
        probe_entry = {"result": DISARM_RESULT_CONFIRMED, "exit_code": 0, "evidence": dict(evidence or {})}
        policy = probe_result_policy(
            "usb_net",
            probe_entry,
            platform=device.platform,
            model=device.model,
            script_name=str((evidence or {}).get("script_name") or "teardown_network"),
            script_version=str((evidence or {}).get("script_version") or "1.0.0"),
            package_sha256=None,
            token_valid=True,
        )
        if not (policy["trusted"] and policy["result"] == DISARM_RESULT_CONFIRMED):
            db.rollback()
            raise UnprocessableEntity({
                "code": "UNVERIFIED_PROBE_RESULT",
                "family": "usb_net",
                "reason": policy.get("reason") or "usbnet_dual_readback_incomplete",
                "observed": policy.get("observed"),
            })
        session.status = "RELEASED"
        session.release_reason = "manual_released"
        session.released_at = ts
        session.updated_at = ts
        _set_family_return_state(
            device,
            "usb_net",
            state=FAMILY_RETURN_RETURNED,
            source=RETURN_SOURCE_DISCHARGE,
            now=ts,
            extra={"manual_session_id": session.id, "disarm_result": DISARM_RESULT_CONFIRMED},
        )
        open_rows = _open_debts_sync(db, device.id)
        if not open_rows:
            lease.status = LeaseStatus.RELEASED.value
            lease.released_at = ts
            lease_released = True
            fencing_token = None
        else:
            converted = _convert_lease_to_debt_sync(db, device, lease, now=ts)
            converted_to_debt = True
            fencing_token = converted.fencing_token
        _recompute_return_state_from_open_debts(device, open_rows, now=ts)
        db.commit()
        return {
            "session_id": session.id,
            "status": session.status,
            "release_reason": session.release_reason,
            "lease_released": lease_released,
            "converted_to_debt": converted_to_debt,
            "fencing_token": fencing_token,
            "open_debt_count": len(open_rows),
            "idempotent": False,
        }

    # Manual usb_net unconfirmed → RELEASE_FAILED + open usb_net debt + N → N+1 debt lease
    session.status = "RELEASE_FAILED"
    session.updated_at = ts
    _set_family_return_state(
        device,
        "usb_net",
        state=FAMILY_RETURN_DEBT,
        source=RETURN_SOURCE_DISCHARGE,
        now=ts,
        extra={"manual_session_id": session.id, "disarm_result": DISARM_RESULT_FAILED},
    )
    existing_debt = (
        db.execute(
            select(DeviceReturnDebt).where(
                DeviceReturnDebt.device_id == device.id,
                DeviceReturnDebt.family == "usb_net",
                DeviceReturnDebt.source_kind == MANUAL_NETWORK_RETURN_SOURCE_KIND,
                DeviceReturnDebt.source_job_id.is_(None),
                DeviceReturnDebt.state == DEBT_STATE_OPEN,
            )
        )
    ).scalars().first()
    debt_evidence = {
        "session_id": session.id,
        "generation": session.generation,
        "error_message": error_message or "",
        **({"evidence": dict(evidence)} if isinstance(evidence, Mapping) else {}),
    }
    if existing_debt is not None:
        existing_debt.evidence = debt_evidence
    else:
        db.add(
            DeviceReturnDebt(
                device_id=device.id,
                family="usb_net",
                source_kind=MANUAL_NETWORK_RETURN_SOURCE_KIND,
                source_job_id=None,
                source_fencing_token=lease.fencing_token,
                cause=DISARM_RESULT_FAILED,
                state=DEBT_STATE_OPEN,
                created_at=ts,
                evidence=debt_evidence,
            )
        )
    db.flush()
    converted = _convert_lease_to_debt_sync(db, device, lease, now=ts)
    open_rows = _open_debts_sync(db, device.id)
    _recompute_return_state_from_open_debts(device, open_rows, now=ts)
    db.commit()
    return {
        "session_id": session.id,
        "status": session.status,
        "release_reason": session.release_reason,
        "lease_released": False,
        "converted_to_debt": True,
        "fencing_token": converted.fencing_token,
        "open_debt_count": len(open_rows),
        "idempotent": False,
    }


def audit_override_network_session_sync(
    db: Session,
    *,
    session_id: int,
    reason_category: str,
    evidence_ref: str,
    admin_username: str,
    admin_user_id: int | None = None,
    request: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Dedicated admin audit-override transaction (ADR-0066 D4.5 & D3.4).

    Covers ``usb_net`` return-debt sessions, unconverted manual ``usb_net``
    sessions, and offline manual ``wifi`` sessions in a single ``Device FOR UPDATE``
    transaction:
    1. For ``usb_net``: close all open ``usb_net`` debts as ``overridden``, mark
       device ``usb_net`` return state ``unverified`` (``source="override"`` —
       NEVER ``returned``), and enqueue a ``usbnet-revoke`` directive for the host.
    2. Set ``device_network_session.status = 'RELEASED'`` with
       ``release_reason = 'manual_override'`` (or ``'unclaimed_canceled'`` if
       unclaimed ``RESERVED`` with ``lease_id IS NULL``).
    3. Release the active ``MAINTENANCE`` lease (either ADR-0061 debt lease or
       ``manual_network_supply`` lease) ONLY when zero open ADR-0061 debts remain
       across ALL families; otherwise preserve or convert to a debt lease.
    4. Record a strict ``network_session_audit_override`` audit entry.
    """
    reason_clean = (reason_category or "").strip()
    evidence_clean = (evidence_ref or "").strip()
    if not reason_clean or not evidence_clean:
        raise BadRequest({
            "code": "AUDIT_OVERRIDE_REASON_REQUIRED",
            "message": "reason_category and evidence_ref are required",
        })

    ts = _now(now)
    pre_device_id = db.execute(
        select(DeviceNetworkSession.device_id).where(DeviceNetworkSession.id == session_id)
    ).scalar_one_or_none()
    if pre_device_id is None:
        raise NotFound({
            "code": "SESSION_NOT_FOUND",
            "message": f"network session {session_id} not found",
        })

    device = _lock_device_sync(db, int(pre_device_id))
    if device is None:
        raise NotFound({"code": "DEVICE_NOT_FOUND", "message": f"device {pre_device_id} not found"})

    session = db.execute(
        select(DeviceNetworkSession)
        .where(DeviceNetworkSession.id == session_id)
        .with_for_update()
    ).scalar_one_or_none()
    if session is None:
        raise NotFound({
            "code": "SESSION_NOT_FOUND",
            "message": f"network session {session_id} not found",
        })
    if session.status == "RELEASED":
        raise Conflict({
            "code": "SESSION_ALREADY_RELEASED",
            "message": f"network session {session_id} is already RELEASED",
        })

    lease = _active_lease_sync(db, device.id)
    closed_debts = 0
    usbnet_revoke_directive: Optional[dict[str, Any]] = None

    if session.resource_type == "usb_net":
        for row in _open_debts_sync(db, device.id):
            if row.family != "usb_net":
                continue
            row.state = DEBT_STATE_CLOSED
            row.closed_reason = DEBT_CLOSE_OVERRIDDEN
            row.closed_at = ts
            closed_debts += 1
        _set_family_return_state(
            device,
            "usb_net",
            state=FAMILY_RETURN_UNVERIFIED,
            source=RETURN_SOURCE_OVERRIDE,
            now=ts,
            extra={
                "reason_category": reason_clean,
                "evidence_ref": evidence_clean,
                "session_id": session.id,
            },
        )
        if session.generation is not None:
            _mark_usbnet_revoke_pending(session, now=ts)
            usbnet_revoke_directive = {
                "session_id": session.id,
                "generation": session.generation,
                "device_serial": device.serial,
                "host_id": session.host_id,
                "usb_devpath": session.usb_devpath,
            }

    if (
        session.status == "RESERVED"
        and session.lease_id is None
        and session.generation is None
    ):
        session.status = "RELEASED"
        session.release_reason = "unclaimed_canceled"
    else:
        session.status = "RELEASED"
        session.release_reason = "manual_override"
    session.released_at = ts
    session.updated_at = ts
    db.flush()

    remaining_debts = _open_debts_sync(db, device.id)
    lease_released = False
    lease_converted_to_debt = False

    if not remaining_debts:
        if _is_debt_lease(lease) or _is_manual_network_lease(lease):
            assert lease is not None
            lease.status = LeaseStatus.RELEASED.value
            lease.released_at = ts
            lease_released = True
            db.flush()
    else:
        if _is_manual_network_lease(lease):
            assert lease is not None
            _convert_lease_to_debt_sync(db, device, lease, now=ts)
            lease_converted_to_debt = True

    _recompute_return_state_from_open_debts(device, remaining_debts, now=ts)

    record_audit(
        db,
        action="network_session_audit_override",
        resource_type="device_network_session",
        resource_id=session.id,
        details={
            "device_id": device.id,
            "device_serial": device.serial,
            "host_id": session.host_id,
            "resource_type": session.resource_type,
            "source": session.source,
            "generation": session.generation,
            "reason_category": reason_clean,
            "evidence_ref": evidence_clean,
            "closed_debt_count": closed_debts,
            "lease_released": lease_released,
            "lease_converted_to_debt": lease_converted_to_debt,
            "open_debt_count": len(remaining_debts),
            "usbnet_revoke_requested": usbnet_revoke_directive is not None,
        },
        user_id=admin_user_id,
        username=admin_username,
        request=request,
        strict=True,
    )
    db.commit()

    pool = db.get(ResourcePool, session.resource_pool_id)
    return {
        "session": _serialize_session(
            session,
            device_serial=device.serial,
            pool_name=pool.name if pool else None,
        ),
        "closed_debt_count": closed_debts,
        "lease_released": lease_released,
        "lease_converted_to_debt": lease_converted_to_debt,
        "open_debt_count": len(remaining_debts),
        "usbnet_revoke_directive": usbnet_revoke_directive,
    }


# ── Heartbeat directives & Agent revoke ack ──────────────────────────────────


def pending_manual_network_releases_for_host_sync(
    db: Session,
    host_id: str,
) -> list[dict[str, Any]]:
    """Heartbeat payload: manual sessions awaiting Agent release/reconnect callback."""
    rows = (
        db.execute(
            select(DeviceNetworkSession, Device, DeviceLease)
            .join(Device, Device.id == DeviceNetworkSession.device_id)
            .join(
                DeviceLease,
                (DeviceLease.id == DeviceNetworkSession.lease_id)
                & (DeviceLease.status == LeaseStatus.ACTIVE.value),
            )
            .where(
                DeviceNetworkSession.host_id == host_id,
                DeviceNetworkSession.source == "manual",
                DeviceNetworkSession.status.in_(("RELEASING", "RELEASE_FAILED")),
                DeviceLease.lease_type == LeaseType.MAINTENANCE.value,
                DeviceLease.reason == MANUAL_NETWORK_LEASE_REASON,
                Device.retired_at.is_(None),
            )
            .order_by(DeviceNetworkSession.id.asc())
        )
    ).all()
    out: list[dict[str, Any]] = []
    for sess, device, lease in rows:
        if lease.lease_generation != sess.generation:
            continue
        out.append(
            {
                "session_id": sess.id,
                "device_id": device.id,
                "serial": device.serial,
                "resource_type": sess.resource_type,
                "egress": sess.egress,
                "generation": sess.generation,
                "status": sess.status,
                "usb_devpath": sess.usb_devpath,
                "prior_network_snapshot": sess.prior_network_snapshot,
            }
        )
    return out


def pending_usbnet_revokes_for_host_sync(
    db: Session,
    host_id: str,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Heartbeat payload: overridden ``usb_net`` sessions awaiting ``usbnet-revoke``."""
    ts = _now(now)
    cutoff = ts - timedelta(seconds=_USBNET_REVOKE_WINDOW_SECONDS)
    rows = (
        db.execute(
            select(DeviceNetworkSession, Device.serial)
            .join(Device, Device.id == DeviceNetworkSession.device_id)
            .where(
                DeviceNetworkSession.host_id == host_id,
                DeviceNetworkSession.resource_type == "usb_net",
                DeviceNetworkSession.status == "RELEASED",
                DeviceNetworkSession.release_reason == "manual_override",
                DeviceNetworkSession.generation.is_not(None),
                DeviceNetworkSession.released_at >= cutoff,
            )
            .order_by(DeviceNetworkSession.id.asc())
        )
    ).all()
    out: list[dict[str, Any]] = []
    for sess, serial in rows:
        snap = sess.prior_network_snapshot if isinstance(sess.prior_network_snapshot, Mapping) else {}
        if not snap.get(_USBNET_REVOKE_PENDING_KEY):
            continue
        out.append(
            {
                "session_id": sess.id,
                "generation": sess.generation,
                "device_serial": serial,
                "usb_devpath": sess.usb_devpath,
            }
        )
    return out


def ack_usbnet_revoke_sync(
    db: Session,
    *,
    session_id: int,
    host_id: str,
    generation: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Agent confirms ``stp-agent-priv usbnet-revoke`` completed for ``session_id``."""
    ts = _now(now)
    session = db.execute(
        select(DeviceNetworkSession)
        .where(DeviceNetworkSession.id == session_id)
        .with_for_update()
    ).scalar_one_or_none()
    if session is None:
        raise NotFound({
            "code": "SESSION_NOT_FOUND",
            "message": f"network session {session_id} not found",
        })
    if session.host_id != host_id or session.generation != generation:
        raise Conflict({
            "code": "SESSION_REVOKE_MISMATCH",
            "message": f"session {session_id} host/generation mismatch",
        })
    snap = dict(session.prior_network_snapshot or {})
    snap[_USBNET_REVOKE_PENDING_KEY] = False
    snap[_USBNET_REVOKE_ACKED_AT_KEY] = ts.isoformat()
    session.prior_network_snapshot = snap
    flag_modified(session, "prior_network_snapshot")
    session.updated_at = ts
    db.commit()
    return {
        "session_id": session.id,
        "generation": session.generation,
        "acknowledged": True,
    }
