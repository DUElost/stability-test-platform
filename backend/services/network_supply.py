"""Unified network supply service: manual enable/renew, session listing,
unclaimed reservation cancellation, and Agent ``usbnet_grant`` renewal
(ADR-0066 D2, D5.3, D6.3 / B13 U1).

Locking order strictly aligns with ``admission_pump.admission_transaction``:
    ``Host FOR UPDATE`` → ``Device FOR UPDATE`` → ``ResourcePool FOR UPDATE``
so manual enable and PlanRun admission (offline / flash / VBUS / networked)
serialize atomically without deadlock risk.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.core.settings.network_supply import get_network_supply_settings
from backend.models.device_lease import DeviceLease
from backend.models.enums import (
    DeviceStatus,
    HostStatus,
    JobStatus,
    LeaseStatus,
    LeaseType,
    PlanRunStatus,
)
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.network_session import (
    ACTIVE_NETWORK_SESSION_STATUSES,
    DeviceNetworkSession,
)
from backend.models.plan_run import PlanRun
from backend.models.resource_pool import ResourcePool
from backend.services.audit_writer import record_audit
from backend.services.device_return import devices_with_open_debt_sync
from backend.services.errors import (
    BadRequest,
    Conflict,
    Gone,
    NotFound,
    ServiceUnavailable,
)
from backend.services.network_grant import (
    UsbnetEgressCooldownError,
    UsbnetGrantError,
    UsbnetGrantKeyNotConfiguredError,
    check_egress_cooldown,
    egress_cooldown_barrier,
    merge_min_allowed_iat_by_egress,
    normalize_min_allowed_iat_by_egress,
    sign_usbnet_grant,
)
from backend.services.plan_run_context import abort_pending_job_ids, run_abort_pending
from backend.services.resource_pool import (
    ACTIVE_JOB_STATUSES,
    VALID_RESOURCE_TYPES,
    current_host_usbnet_device_sets_sync,
    current_pool_device_sets_sync,
    effective_host_usbnet_hard_cap,
    effective_pool_host_cap,
    pool_egress,
)

logger = logging.getLogger(__name__)

MANUAL_NETWORK_LEASE_REASON = "manual_network_supply"


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def host_supports_usbnet(host: Host | None) -> bool:
    """True when the host's latest heartbeat declared ``usb_net`` capability."""
    if host is None:
        return False
    caps = host.agent_capabilities
    return isinstance(caps, list) and "usb_net" in caps


def get_host_egress_barrier(host: Host | None, egress: str) -> int:
    """Return host's ``min_allowed_iat`` cooldown barrier for ``egress`` (missing → 0)."""
    if host is None:
        return 0
    return egress_cooldown_barrier(host.usbnet_min_allowed_iat_by_egress, egress)


def update_host_egress_barriers_sync(
    host: Host,
    reported: Mapping[str, Any] | None,
    *,
    replace: bool = False,
) -> bool:
    """Persist Agent-reported ``min_allowed_iat_by_egress`` onto ``Host``."""
    if not isinstance(reported, Mapping):
        return False
    normalized = normalize_min_allowed_iat_by_egress(reported)
    if replace:
        new_map = normalized
    else:
        new_map = merge_min_allowed_iat_by_egress(
            host.usbnet_min_allowed_iat_by_egress, normalized,
        )
    if new_map == (host.usbnet_min_allowed_iat_by_egress or {}):
        return False
    host.usbnet_min_allowed_iat_by_egress = new_map
    flag_modified(host, "usbnet_min_allowed_iat_by_egress")
    return True


def resolve_device_usb_devpath(
    device: Device | None,
    host: Host | None = None,
) -> Optional[str]:
    """Resolve a device's sysfs ``usb_devpath`` from the latest heartbeat USB topology."""
    if device is None:
        return None
    dev_extra = device.extra if isinstance(device.extra, dict) else {}
    raw_dev = dev_extra.get("usb_devpath")
    if isinstance(raw_dev, str) and raw_dev.strip():
        return raw_dev.strip()

    if host is not None and isinstance(host.extra, dict):
        serial = device.serial
        capacity = host.extra.get("capacity")
        if isinstance(capacity, dict):
            devpaths = capacity.get("usb_devpaths")
            if isinstance(devpaths, dict):
                val = devpaths.get(serial)
                if isinstance(val, str) and val.strip():
                    return val.strip()
        for key in ("usb_devpaths", "usb_topology"):
            mapping = host.extra.get(key)
            if isinstance(mapping, dict):
                val = mapping.get(serial)
                if isinstance(val, str) and val.strip():
                    return val.strip()
    return None


def _pool_allows_host(pool: ResourcePool, host: Host) -> bool:
    group = (pool.host_group or "").strip()
    if not group:
        return True
    keys = {host.id}
    if host.name:
        keys.add(host.name)
    if host.hostname:
        keys.add(host.hostname)
    return group in keys


def _serialize_session(
    session: DeviceNetworkSession,
    *,
    device_serial: str | None = None,
    pool_name: str | None = None,
    usbnet_grant: str | None = None,
    usbnet_grant_exp: int | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
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
    if usbnet_grant is not None:
        out["usbnet_grant"] = usbnet_grant
        out["usbnet_grant_exp"] = usbnet_grant_exp
    return out


def release_unclaimed_network_sessions_sync(
    db: Session,
    job_ids: Sequence[int],
    *,
    now: datetime | None = None,
) -> int:
    """Point 3 handover (ADR-0066 D2.4): cancel ``RESERVED`` sessions for unclaimed ``PENDING`` jobs.

    Runs inside the caller's transaction where ``PENDING`` jobs move to terminal
    status (``ABORTED`` / ``FAILED``). Transitions ``RESERVED → RELEASED`` with
    ``release_reason='unclaimed_canceled'`` while keeping ``lease_id=NULL`` and
    ``generation=NULL`` (passing ``ck_device_network_session_lease_binding``),
    without touching any ``DeviceLease``.
    """
    ids = [int(jid) for jid in job_ids if jid is not None]
    if not ids:
        return 0
    ts = now or datetime.now(timezone.utc)
    result = db.execute(
        update(DeviceNetworkSession)
        .where(
            DeviceNetworkSession.job_instance_id.in_(ids),
            DeviceNetworkSession.source == "job",
            DeviceNetworkSession.status == "RESERVED",
            DeviceNetworkSession.lease_id.is_(None),
            DeviceNetworkSession.generation.is_(None),
        )
        .values(
            status="RELEASED",
            release_reason="unclaimed_canceled",
            released_at=ts,
            updated_at=ts,
        )
        .execution_options(synchronize_session=False)
    )
    count = int(result.rowcount or 0)
    if count:
        logger.info(
            "network_session_unclaimed_canceled jobs=%d sessions=%d",
            len(ids), count,
        )
    return count


def enable_manual_network_supply_sync(
    db: Session,
    *,
    device_id: int,
    resource_pool_id: int,
    admin_username: str,
    admin_user_id: int | None = None,
    ttl_seconds: int | None = None,
    now_ts: float | None = None,
    request: Any = None,
) -> dict[str, Any]:
    """Atomically enable manual debug network supply on an idle device (ADR-0066 D2.5).

    Lock order: ``Host FOR UPDATE`` → ``Device FOR UPDATE`` → ``ResourcePool FOR UPDATE``.
    """
    settings = get_network_supply_settings()
    ttl = (
        settings.stp_manual_network_default_ttl_seconds
        if ttl_seconds is None
        else int(ttl_seconds)
    )
    if ttl < 300 or ttl > settings.stp_manual_network_max_ttl_seconds:
        raise BadRequest({
            "code": "INVALID_MANUAL_TTL",
            "message": (
                f"ttl_seconds={ttl} out of allowed range "
                f"[300, {settings.stp_manual_network_max_ttl_seconds}]"
            ),
        })

    # Pre-read device.host_id to acquire Host lock before Device lock.
    pre_host_id = db.execute(
        select(Device.host_id).where(Device.id == device_id)
    ).scalar_one_or_none()
    if pre_host_id is None:
        exists = db.execute(
            select(Device.id).where(Device.id == device_id)
        ).scalar_one_or_none()
        if exists is None:
            raise NotFound({"code": "DEVICE_NOT_FOUND", "message": f"device {device_id} not found"})
        raise BadRequest({"code": "DEVICE_NO_HOST", "message": f"device {device_id} has no host"})

    # 1. Host FOR UPDATE
    host = db.execute(
        select(Host)
        .where(Host.id == pre_host_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if host is None:
        db.rollback()
        raise NotFound({"code": "HOST_NOT_FOUND", "message": f"host {pre_host_id} not found"})

    # 2. Device FOR UPDATE
    device = db.execute(
        select(Device)
        .where(Device.id == device_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if device is None:
        db.rollback()
        raise NotFound({"code": "DEVICE_NOT_FOUND", "message": f"device {device_id} not found"})
    if device.host_id != host.id:
        db.rollback()
        raise Conflict({
            "code": "DEVICE_HOST_CHANGED",
            "message": "device migrated to another host concurrently; retry",
        })

    # 3. ResourcePool FOR UPDATE
    pool = db.execute(
        select(ResourcePool)
        .where(ResourcePool.id == resource_pool_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if pool is None or not pool.is_active or pool.resource_type not in VALID_RESOURCE_TYPES:
        db.rollback()
        raise BadRequest({
            "code": "NETWORK_POOL_UNAVAILABLE",
            "message": f"resource_pool {resource_pool_id} is missing, inactive, or invalid",
        })

    egress = pool_egress(pool.config)
    if pool.resource_type == "usb_net" and egress == "vpn":
        db.rollback()
        raise BadRequest({
            "code": "USBNET_VPN_UNSUPPORTED_IN_PHASE1",
            "message": "usb_net + vpn is disabled in Phase 1",
        })

    if not _pool_allows_host(pool, host):
        db.rollback()
        raise BadRequest({
            "code": "POOL_HOST_GROUP_MISMATCH",
            "message": f"pool {pool.id} is not allowed on host {host.id!r}",
        })

    if host.retired_at is not None or host.status != HostStatus.ONLINE.value:
        db.rollback()
        raise Conflict({
            "code": "HOST_UNAVAILABLE",
            "message": f"host {host.id!r} is retired or offline",
        })
    if device.retired_at is not None or device.status != DeviceStatus.ONLINE.value:
        db.rollback()
        raise Conflict({
            "code": "DEVICE_UNAVAILABLE",
            "message": f"device {device.id} is retired or not ONLINE (status={device.status})",
        })

    # Check active JobInstance
    active_job = db.execute(
        select(JobInstance.id, JobInstance.status)
        .where(
            JobInstance.device_id == device.id,
            JobInstance.status.in_(ACTIVE_JOB_STATUSES),
        )
        .limit(1)
    ).first()
    if active_job is not None:
        db.rollback()
        raise Conflict({
            "code": "DEVICE_BUSY_JOB",
            "message": f"device {device.id} has an active job #{active_job.id} ({active_job.status})",
            "job_id": active_job.id,
        })

    # Check open return debt (ADR-0061)
    if device.id in devices_with_open_debt_sync(db, [device.id]):
        db.rollback()
        raise Conflict({
            "code": "DEVICE_RETURN_DEBT",
            "message": f"device {device.id} has open return debt; discharge or override first",
        })

    # Check ANY active DeviceLease (JOB / SCRIPT / MAINTENANCE including return-debt or flash/VBUS)
    active_lease = db.execute(
        select(DeviceLease)
        .where(
            DeviceLease.device_id == device.id,
            DeviceLease.status == LeaseStatus.ACTIVE.value,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if active_lease is not None:
        db.rollback()
        raise Conflict({
            "code": "DEVICE_LEASE_CONFLICT",
            "message": (
                f"device {device.id} already holds an ACTIVE {active_lease.lease_type} lease "
                f"(id={active_lease.id}, reason={active_lease.reason!r})"
            ),
            "lease_id": active_lease.id,
            "lease_type": active_lease.lease_type,
            "reason": active_lease.reason,
        })

    # Check unreleased DeviceNetworkSession
    active_session = db.execute(
        select(DeviceNetworkSession.id, DeviceNetworkSession.status)
        .where(
            DeviceNetworkSession.device_id == device.id,
            DeviceNetworkSession.status.in_(ACTIVE_NETWORK_SESSION_STATUSES),
        )
        .limit(1)
    ).first()
    if active_session is not None:
        db.rollback()
        raise Conflict({
            "code": "ACTIVE_NETWORK_SESSION_EXISTS",
            "message": (
                f"device {device.id} already has an unreleased network session "
                f"#{active_session.id} ({active_session.status})"
            ),
            "session_id": active_session.id,
        })

    # Quota checks (ADR-0066 D2.7)
    pool_devices = current_pool_device_sets_sync(db, [pool.id]).get(pool.id, set())
    if len(pool_devices | {device.id}) > pool.max_concurrent_devices:
        db.rollback()
        raise Conflict({
            "code": "NETWORK_POOL_CAPACITY_EXCEEDED",
            "message": (
                f"pool {pool.id} reached max_concurrent_devices="
                f"{pool.max_concurrent_devices}"
            ),
        })

    usb_devpath: Optional[str] = None
    if pool.resource_type == "usb_net":
        if not host_supports_usbnet(host):
            db.rollback()
            raise BadRequest({
                "code": "HOST_USBNET_UNSUPPORTED",
                "message": f"host {host.id!r} has not declared 'usb_net' capability",
            })
        usb_devpath = resolve_device_usb_devpath(device, host)
        if not usb_devpath:
            db.rollback()
            raise BadRequest({
                "code": "USB_DEVPATH_UNAVAILABLE",
                "message": (
                    f"device {device.serial} has no reported heartbeat usb_devpath on host {host.id!r}"
                ),
            })

        host_usb_map, host_pool_map = current_host_usbnet_device_sets_sync(db, [host.id])
        host_cap = effective_host_usbnet_hard_cap(host)
        if len(host_usb_map.get(host.id, set()) | {device.id}) > host_cap:
            db.rollback()
            raise Conflict({
                "code": "HOST_USBNET_CAPACITY_EXCEEDED",
                "message": f"host {host.id!r} reached usbnet_max_devices={host_cap}",
            })
        pool_host_cap = effective_pool_host_cap(host, pool)
        if len(host_pool_map.get((host.id, pool.id), set()) | {device.id}) > pool_host_cap:
            db.rollback()
            raise Conflict({
                "code": "POOL_HOST_CAPACITY_EXCEEDED",
                "message": (
                    f"pool {pool.id} on host {host.id!r} reached per-host cap {pool_host_cap}"
                ),
            })

        # Cooldown gate BEFORE writing lease/session (truthful iat, never forged)
        try:
            check_egress_cooldown(
                host_id=host.id,
                egress=egress,
                min_allowed_iat_by_egress=host.usbnet_min_allowed_iat_by_egress,
                now_ts=now_ts,
            )
        except UsbnetEgressCooldownError as exc:
            db.rollback()
            raise ServiceUnavailable(
                exc.detail(),
                headers={"Retry-After": str(exc.retry_after_seconds)},
            ) from exc

    now = (
        datetime.fromtimestamp(now_ts, tz=timezone.utc)
        if now_ts is not None
        else datetime.now(timezone.utc)
    )
    expires_at = now + timedelta(seconds=ttl)

    # Atomically advance device.lease_generation + create MAINTENANCE lease + session
    new_gen = int(device.lease_generation or 0) + 1
    device.lease_generation = new_gen
    lease = DeviceLease(
        device_id=device.id,
        job_id=None,
        host_id=host.id,
        lease_type=LeaseType.MAINTENANCE.value,
        status=LeaseStatus.ACTIVE.value,
        fencing_token=f"{device.id}:{new_gen}",
        lease_generation=new_gen,
        agent_instance_id=host.last_agent_instance_id or "",
        reason=MANUAL_NETWORK_LEASE_REASON,
        holder=admin_username,
        acquired_at=now,
        renewed_at=now,
        expires_at=expires_at,
    )
    db.add(lease)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict({
            "code": "DEVICE_LEASE_CONFLICT",
            "message": f"device {device.id} lease acquired concurrently",
        }) from exc

    session = DeviceNetworkSession(
        device_id=device.id,
        host_id=host.id,
        resource_pool_id=pool.id,
        resource_type=pool.resource_type,
        egress=egress,
        source="manual",
        plan_run_id=None,
        job_instance_id=None,
        lease_id=lease.id,
        generation=new_gen,
        legacy_path=False,
        status="PROVISIONING",
        usb_devpath=usb_devpath,
        created_by=admin_username,
        expires_at=expires_at,
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict({
            "code": "ACTIVE_NETWORK_SESSION_EXISTS",
            "message": f"device {device.id} network session created concurrently",
        }) from exc

    grant_token: Optional[str] = None
    grant_exp: Optional[int] = None
    if pool.resource_type == "usb_net":
        try:
            signed = sign_usbnet_grant(
                session_id=session.id,
                generation=new_gen,
                host_id=host.id,
                device_serial=device.serial,
                usb_devpath=usb_devpath or "",
                egress=egress,
                min_allowed_iat_by_egress=host.usbnet_min_allowed_iat_by_egress,
                now_ts=now_ts,
            )
            grant_token = signed.token
            grant_exp = signed.exp
        except UsbnetEgressCooldownError as exc:
            db.rollback()
            raise ServiceUnavailable(
                exc.detail(),
                headers={"Retry-After": str(exc.retry_after_seconds)},
            ) from exc
        except UsbnetGrantKeyNotConfiguredError as exc:
            db.rollback()
            raise ServiceUnavailable({
                "code": "USBNET_GRANT_KEY_NOT_CONFIGURED",
                "message": str(exc),
            }) from exc
        except UsbnetGrantError as exc:
            db.rollback()
            raise BadRequest({
                "code": "USBNET_GRANT_SIGN_FAILED",
                "message": str(exc),
            }) from exc

    record_audit(
        db,
        action="manual_network_supply_enable",
        resource_type="device_network_session",
        resource_id=session.id,
        details={
            "device_id": device.id,
            "device_serial": device.serial,
            "host_id": host.id,
            "resource_pool_id": pool.id,
            "resource_type": pool.resource_type,
            "egress": egress,
            "lease_id": lease.id,
            "generation": new_gen,
            "ttl_seconds": ttl,
        },
        user_id=admin_user_id,
        username=admin_username,
        request=request,
    )
    db.commit()
    return _serialize_session(
        session,
        device_serial=device.serial,
        pool_name=pool.name,
        usbnet_grant=grant_token,
        usbnet_grant_exp=grant_exp,
    )


def renew_manual_network_session_sync(
    db: Session,
    *,
    session_id: int,
    extend_seconds: int,
    admin_username: str,
    admin_user_id: int | None = None,
    now_ts: float | None = None,
    request: Any = None,
) -> dict[str, Any]:
    """Extend ``expires_at`` on an active manual network-supply session (ADR-0066 D2.6)."""
    settings = get_network_supply_settings()
    if extend_seconds < 300:
        raise BadRequest({
            "code": "INVALID_EXTEND_SECONDS",
            "message": "extend_seconds must be at least 300",
        })

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
            "message": "only manual network sessions can be renewed via this endpoint",
        })
    if session.status not in ("PROVISIONING", "ACTIVE"):
        raise Conflict({
            "code": "SESSION_NOT_RENEWABLE",
            "message": f"session {session_id} is in status={session.status!r}",
        })

    now = (
        datetime.fromtimestamp(now_ts, tz=timezone.utc)
        if now_ts is not None
        else datetime.now(timezone.utc)
    )
    current_exp = _as_utc(session.expires_at)
    if current_exp is None or current_exp <= now:
        raise Gone({
            "code": "MANUAL_SESSION_EXPIRED",
            "message": f"manual session {session_id} has already expired",
        })

    lease = db.execute(
        select(DeviceLease)
        .where(DeviceLease.id == session.lease_id)
        .with_for_update()
    ).scalar_one_or_none()
    if (
        lease is None
        or lease.status != LeaseStatus.ACTIVE.value
        or lease.lease_generation != session.generation
    ):
        raise Conflict({
            "code": "LEASE_INVALID",
            "message": f"bound lease for session {session_id} is no longer active",
        })

    created_at = _as_utc(session.created_at) or now
    max_exp = created_at + timedelta(seconds=settings.stp_manual_network_max_ttl_seconds)
    new_exp = max(now, current_exp) + timedelta(seconds=extend_seconds)
    if new_exp > max_exp:
        raise BadRequest({
            "code": "MANUAL_TTL_LIMIT_EXCEEDED",
            "message": (
                f"manual session total duration cannot exceed "
                f"{settings.stp_manual_network_max_ttl_seconds}s"
            ),
        })

    session.expires_at = new_exp
    session.updated_at = now
    lease.expires_at = new_exp
    lease.renewed_at = now

    record_audit(
        db,
        action="manual_network_supply_renew",
        resource_type="device_network_session",
        resource_id=session.id,
        details={
            "extend_seconds": extend_seconds,
            "new_expires_at": new_exp.isoformat(),
        },
        user_id=admin_user_id,
        username=admin_username,
        request=request,
    )
    db.commit()

    device = db.get(Device, session.device_id)
    pool = db.get(ResourcePool, session.resource_pool_id)
    return _serialize_session(
        session,
        device_serial=device.serial if device else None,
        pool_name=pool.name if pool else None,
    )


def list_network_sessions_sync(
    db: Session,
    *,
    include_released: bool = False,
    host_id: str | None = None,
    resource_type: str | None = None,
    source: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List managed ``device_network_session`` rows (default: unreleased active sessions)."""
    stmt = (
        select(DeviceNetworkSession, Device.serial, ResourcePool.name)
        .join(Device, Device.id == DeviceNetworkSession.device_id)
        .join(ResourcePool, ResourcePool.id == DeviceNetworkSession.resource_pool_id)
    )
    if status:
        stmt = stmt.where(DeviceNetworkSession.status == status)
    elif not include_released:
        stmt = stmt.where(
            DeviceNetworkSession.status.in_(ACTIVE_NETWORK_SESSION_STATUSES)
        )
    if host_id:
        stmt = stmt.where(DeviceNetworkSession.host_id == host_id)
    if resource_type:
        stmt = stmt.where(DeviceNetworkSession.resource_type == resource_type)
    if source:
        stmt = stmt.where(DeviceNetworkSession.source == source)

    rows = db.execute(
        stmt.order_by(
            DeviceNetworkSession.created_at.desc(),
            DeviceNetworkSession.id.desc(),
        )
    ).all()
    return [
        _serialize_session(sess, device_serial=serial, pool_name=pool_name)
        for sess, serial, pool_name in rows
    ]


def renew_usbnet_grant_sync(
    db: Session,
    *,
    session_id: int,
    host_id: str,
    generation: int,
    usb_devpath: str,
    min_allowed_iat_by_egress: Mapping[str, Any] | None = None,
    now_ts: float | None = None,
) -> dict[str, Any]:
    """Renew short-lived Ed25519 ``usbnet_grant`` for an active ``usb_net`` session.

    Enforces ADR-0066 D5.3 & D6.3 (B13 v1.3):
    - Persists any Agent-reported ``min_allowed_iat_by_egress`` onto ``Host``;
    - Rejects released (410), non-PROVISIONING/ACTIVE (409), wrong host/generation (409),
      inactive/expired lease (409), expired manual session (410), non-RUNNING /
      aborting / finishing job (409);
    - Rejects during cooldown (``iat <= barrier``) with 503 ``USBNET_EGRESS_COOLDOWN``
      + ``Retry-After`` without forging future ``iat``.
    """
    host = db.execute(
        select(Host)
        .where(Host.id == host_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if host is not None and isinstance(min_allowed_iat_by_egress, Mapping):
        if update_host_egress_barriers_sync(host, min_allowed_iat_by_egress):
            # Persist barrier update even if this renewal is subsequently rejected.
            db.commit()
            host = db.execute(
                select(Host)
                .where(Host.id == host_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one_or_none()

    session = db.execute(
        select(DeviceNetworkSession)
        .where(DeviceNetworkSession.id == session_id)
        .with_for_update()
    ).scalar_one_or_none()
    if session is None:
        db.rollback()
        raise NotFound({
            "code": "SESSION_NOT_FOUND",
            "message": f"network session {session_id} not found",
        })
    if session.resource_type != "usb_net":
        db.rollback()
        raise Conflict({
            "code": "NOT_USBNET_SESSION",
            "message": f"session {session_id} has resource_type={session.resource_type!r}",
        })
    if session.status == "RELEASED":
        db.rollback()
        raise Gone({
            "code": "SESSION_RELEASED",
            "message": f"session {session_id} is already RELEASED",
        })
    if session.status not in ("PROVISIONING", "ACTIVE"):
        db.rollback()
        raise Conflict({
            "code": "SESSION_NOT_RENEWABLE",
            "message": f"session {session_id} status={session.status!r} cannot renew grant",
        })
    if session.host_id != host_id:
        db.rollback()
        raise Conflict({
            "code": "HOST_MISMATCH",
            "message": f"session {session_id} belongs to host {session.host_id!r}, not {host_id!r}",
        })
    if session.generation != generation:
        db.rollback()
        raise Conflict({
            "code": "GENERATION_MISMATCH",
            "message": (
                f"session {session_id} generation={session.generation} "
                f"does not match requested generation={generation}"
            ),
        })

    now = (
        datetime.fromtimestamp(now_ts, tz=timezone.utc)
        if now_ts is not None
        else datetime.now(timezone.utc)
    )

    lease = (
        db.get(DeviceLease, session.lease_id)
        if session.lease_id is not None
        else None
    )
    lease_exp = _as_utc(lease.expires_at) if lease is not None else None
    if (
        lease is None
        or lease.status != LeaseStatus.ACTIVE.value
        or lease.lease_generation != session.generation
        or lease_exp is None
        or lease_exp <= now
    ):
        db.rollback()
        raise Conflict({
            "code": "LEASE_INVALID_OR_EXPIRED",
            "message": f"bound lease for session {session_id} is inactive or expired",
        })

    if session.source == "manual":
        sess_exp = _as_utc(session.expires_at)
        if sess_exp is None or sess_exp <= now:
            db.rollback()
            raise Gone({
                "code": "MANUAL_SESSION_EXPIRED",
                "message": f"manual session {session_id} has expired",
            })
    else:
        job = (
            db.get(JobInstance, session.job_instance_id)
            if session.job_instance_id is not None
            else None
        )
        if job is None or job.status != JobStatus.RUNNING.value:
            db.rollback()
            raise Conflict({
                "code": "JOB_NOT_RUNNING",
                "message": f"job for session {session_id} is not RUNNING",
            })
        if job.finishing_guard is not None or job.disarm_guard is not None:
            db.rollback()
            raise Conflict({
                "code": "JOB_FINISHING",
                "message": f"job {job.id} is already in finishing/disarm guard",
            })
        plan_run = db.get(PlanRun, job.plan_run_id) if job.plan_run_id else None
        if plan_run is not None:
            if plan_run.status != PlanRunStatus.RUNNING.value:
                db.rollback()
                raise Conflict({
                    "code": "PLAN_RUN_NOT_RUNNING",
                    "message": f"plan_run {plan_run.id} is not RUNNING ({plan_run.status})",
                })
            if run_abort_pending(plan_run.run_context) or job.id in abort_pending_job_ids(
                plan_run.run_context, [(job.id, job.host_id)],
            ):
                db.rollback()
                raise Conflict({
                    "code": "JOB_ABORTING",
                    "message": f"job {job.id} has an active abort request",
                })

    device = db.get(Device, session.device_id)
    if device is None:
        db.rollback()
        raise NotFound({"code": "DEVICE_NOT_FOUND", "message": f"device {session.device_id} not found"})

    barriers = merge_min_allowed_iat_by_egress(
        host.usbnet_min_allowed_iat_by_egress if host is not None else None,
        min_allowed_iat_by_egress,
    )
    try:
        signed = sign_usbnet_grant(
            session_id=session.id,
            generation=int(session.generation),
            host_id=session.host_id,
            device_serial=device.serial,
            usb_devpath=usb_devpath.strip(),
            egress=session.egress,
            min_allowed_iat_by_egress=barriers,
            now_ts=now_ts,
        )
    except UsbnetEgressCooldownError as exc:
        db.rollback()
        raise ServiceUnavailable(
            exc.detail(),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except UsbnetGrantKeyNotConfiguredError as exc:
        db.rollback()
        raise ServiceUnavailable({
            "code": "USBNET_GRANT_KEY_NOT_CONFIGURED",
            "message": str(exc),
        }) from exc
    except UsbnetGrantError as exc:
        db.rollback()
        raise BadRequest({
            "code": "USBNET_GRANT_SIGN_FAILED",
            "message": str(exc),
        }) from exc

    session.usb_devpath = usb_devpath.strip()
    session.updated_at = now
    db.commit()
    return {
        "session_id": session.id,
        "generation": int(session.generation),
        "host_id": session.host_id,
        "device_serial": device.serial,
        "usb_devpath": session.usb_devpath,
        "egress": session.egress,
        "iat": signed.iat,
        "exp": signed.exp,
        "usbnet_grant": signed.token,
    }
