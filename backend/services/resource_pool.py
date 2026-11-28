"""Unified network supply resource pool service (ADR-0066 D1 / D2.7 / D3.5).

Covers both ``wifi`` and ``usb_net`` pools with orthogonal ``config.egress``
(``"direct"`` | ``"vpn"``), fail-closed whitelist redaction, Phase 1 egress
gating (rejecting ``usb_net + vpn`` while allowing ``wifi + vpn``), and
ADR-0066 D2.7 union-deduplicated global + per-host quota accounting.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Sequence

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.models.enums import JobStatus
from backend.models.host import Host
from backend.models.job import JobInstance
from backend.models.network_session import (
    ACTIVE_NETWORK_SESSION_STATUSES,
    DeviceNetworkSession,
)
from backend.models.resource_pool import ResourceAllocation, ResourcePool
from backend.services.errors import BadRequest

logger = logging.getLogger(__name__)

VALID_RESOURCE_TYPES = frozenset({"wifi", "usb_net"})
VALID_EGRESSES = frozenset({"direct", "vpn"})
DEFAULT_EGRESS = "direct"

DEFAULT_USBNET_MAX_DEVICES_PER_HOST = 8
MAX_USBNET_MAX_DEVICES_PER_HOST = 32

# ADR-0066 D3.5 / #955: fail-closed public config whitelist for /available and /loads.
# ``password`` and any future proxy/VPN credentials are NEVER included.
PUBLIC_CONFIG_KEYS = (
    "ssid",
    "band",
    "router_ip",
    "mac_filter",
    "egress",
    "bridge_name",
    "subnet_cidr",
    "max_devices_per_host",
)

# 与 plan_dispatcher_sync.ACTIVE_JOB_STATUSES 保持一致：UNKNOWN grace 期内
# 仍占用资源，池负载/容量须纳入（#154 对齐收口）
ACTIVE_JOB_STATUSES = (
    JobStatus.PENDING.value,
    JobStatus.RUNNING.value,
    JobStatus.UNKNOWN.value,
)


class AllocationError(Exception):
    pass


def pool_egress(config: Dict[str, Any] | None) -> str:
    """Return effective orthogonal egress policy for a pool config (default ``direct``)."""
    raw = (config or {}).get("egress")
    if raw is None or str(raw).strip() == "":
        return DEFAULT_EGRESS
    return str(raw).strip()


def validate_resource_pool_spec(
    resource_type: str,
    config: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """Validate ``resource_type`` and ``config`` against ADR-0066 D1 & D3.3.

    Raises :class:`BadRequest` with structured detail on invalid type, invalid
    egress, Phase 1 disallowed ``usb_net + vpn``, or invalid per-host soft cap.
    """
    rtype = (resource_type or "").strip()
    if rtype not in VALID_RESOURCE_TYPES:
        raise BadRequest({
            "code": "INVALID_RESOURCE_TYPE",
            "message": (
                f"Unsupported resource_type {resource_type!r}; "
                f"allowed: {sorted(VALID_RESOURCE_TYPES)}"
            ),
        })

    cfg = dict(config or {})
    egress = pool_egress(cfg)
    if egress not in VALID_EGRESSES:
        raise BadRequest({
            "code": "INVALID_POOL_EGRESS",
            "message": (
                f"Unsupported config.egress {egress!r}; "
                f"allowed: {sorted(VALID_EGRESSES)}"
            ),
        })
    if rtype == "usb_net" and egress == "vpn":
        raise BadRequest({
            "code": "USBNET_VPN_UNSUPPORTED_IN_PHASE1",
            "message": (
                "resource_type='usb_net' with config.egress='vpn' is disabled in "
                "Phase 1 (ADR-0066 D3.3); use resource_type='wifi' for VPN egress "
                "or config.egress='direct' for usb_net"
            ),
        })

    if "max_devices_per_host" in cfg and cfg["max_devices_per_host"] is not None:
        raw_cap = cfg["max_devices_per_host"]
        if isinstance(raw_cap, bool) or not isinstance(raw_cap, int) or not (
            1 <= raw_cap <= MAX_USBNET_MAX_DEVICES_PER_HOST
        ):
            raise BadRequest({
                "code": "INVALID_POOL_CONFIG",
                "message": (
                    "config.max_devices_per_host must be an integer between "
                    f"1 and {MAX_USBNET_MAX_DEVICES_PER_HOST}"
                ),
            })

    cfg["egress"] = egress
    return cfg


def public_pool_config(config: Dict[str, Any] | None) -> Dict[str, Any]:
    """Strip secret keys using strict whitelist and always expose effective ``egress``."""
    out = {
        key: value
        for key, value in (config or {}).items()
        if key in PUBLIC_CONFIG_KEYS
    }
    out["egress"] = pool_egress(config)
    return out


def effective_host_usbnet_hard_cap(host: Host | None) -> int:
    """Return host-level absolute ``usb_net`` concurrency hard cap (default 8, [1..32])."""
    raw = getattr(host, "usbnet_max_devices", None) if host is not None else None
    if raw is None:
        return DEFAULT_USBNET_MAX_DEVICES_PER_HOST
    try:
        cap = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_USBNET_MAX_DEVICES_PER_HOST
    return max(1, min(MAX_USBNET_MAX_DEVICES_PER_HOST, cap))


def effective_pool_host_cap(host: Host | None, pool: ResourcePool) -> int:
    """Return effective per-host cap for a ``usb_net`` pool: ``min(host_cap, pool_soft_cap)``."""
    hard_cap = effective_host_usbnet_hard_cap(host)
    raw_soft = (pool.config or {}).get("max_devices_per_host")
    if raw_soft is None or isinstance(raw_soft, bool):
        return hard_cap
    try:
        soft_cap = int(raw_soft)
    except (TypeError, ValueError):
        return hard_cap
    if soft_cap < 1:
        return hard_cap
    return min(hard_cap, soft_cap)


def current_pool_device_sets_sync(
    db: Session,
    pool_ids: Sequence[int],
) -> Dict[int, set[int]]:
    """ADR-0066 D2.7: union-deduplicated ``device_id`` sets per pool (sync).

    Combines active ``device_network_session`` rows (including ``RESERVED``,
    ``RELEASING``, ``RELEASE_FAILED``, both ``job`` and ``manual``) and active
    legacy ``resource_allocation`` rows (``JobInstance.status IN ACTIVE_JOB_STATUSES``)
    without double counting devices present in both tables.
    """
    ids = list(pool_ids)
    if not ids:
        return {}
    devices_by_pool: dict[int, set[int]] = {pid: set() for pid in ids}

    session_rows = db.execute(
        select(
            DeviceNetworkSession.resource_pool_id,
            DeviceNetworkSession.device_id,
        ).where(
            DeviceNetworkSession.resource_pool_id.in_(ids),
            DeviceNetworkSession.status.in_(ACTIVE_NETWORK_SESSION_STATUSES),
        )
    ).all()
    for pool_id, device_id in session_rows:
        devices_by_pool.setdefault(pool_id, set()).add(device_id)

    alloc_rows = db.execute(
        select(
            ResourceAllocation.resource_pool_id,
            ResourceAllocation.device_id,
        )
        .join(JobInstance, JobInstance.id == ResourceAllocation.job_instance_id)
        .where(
            ResourceAllocation.resource_pool_id.in_(ids),
            JobInstance.status.in_(ACTIVE_JOB_STATUSES),
        )
    ).all()
    for pool_id, device_id in alloc_rows:
        devices_by_pool.setdefault(pool_id, set()).add(device_id)

    return devices_by_pool


def current_host_usbnet_device_sets_sync(
    db: Session,
    host_ids: Sequence[str],
) -> tuple[Dict[str, set[int]], Dict[tuple[str, int], set[int]]]:
    """Return active ``usb_net`` device sets per host (cross-pool) and per ``(host, pool)``."""
    ids = [hid for hid in host_ids if hid]
    if not ids:
        return {}, {}
    host_devices: dict[str, set[int]] = {hid: set() for hid in ids}
    host_pool_devices: dict[tuple[str, int], set[int]] = {}

    rows = db.execute(
        select(
            DeviceNetworkSession.host_id,
            DeviceNetworkSession.resource_pool_id,
            DeviceNetworkSession.device_id,
        ).where(
            DeviceNetworkSession.host_id.in_(ids),
            DeviceNetworkSession.resource_type == "usb_net",
            DeviceNetworkSession.status.in_(ACTIVE_NETWORK_SESSION_STATUSES),
        )
    ).all()
    for host_id, pool_id, device_id in rows:
        host_devices.setdefault(host_id, set()).add(device_id)
        host_pool_devices.setdefault((host_id, pool_id), set()).add(device_id)

    return host_devices, host_pool_devices


async def list_active_pools(
    db: AsyncSession,
    resource_type: str | None = "wifi",
    host_group: str | None = None,
) -> Sequence[ResourcePool]:
    """Return all active pools, optionally filtered by resource_type and host_group."""
    clauses = [ResourcePool.is_active.is_(True)]
    if resource_type:
        clauses.append(ResourcePool.resource_type == resource_type)
    if host_group:
        clauses.append(ResourcePool.host_group == host_group)

    result = await db.execute(
        select(ResourcePool).where(and_(*clauses)).order_by(ResourcePool.id)
    )
    return result.scalars().all()


async def _current_pool_loads(
    db: AsyncSession,
    pool_ids: list[int],
) -> Dict[int, int]:
    """Return union-deduplicated active device count per pool (ADR-0066 D2.7)."""
    if not pool_ids:
        return {}

    devices_by_pool: dict[int, set[int]] = {pid: set() for pid in pool_ids}

    session_rows = (
        await db.execute(
            select(
                DeviceNetworkSession.resource_pool_id,
                DeviceNetworkSession.device_id,
            ).where(
                DeviceNetworkSession.resource_pool_id.in_(pool_ids),
                DeviceNetworkSession.status.in_(ACTIVE_NETWORK_SESSION_STATUSES),
            )
        )
    ).all()
    for pool_id, device_id in session_rows:
        devices_by_pool.setdefault(pool_id, set()).add(device_id)

    alloc_rows = (
        await db.execute(
            select(
                ResourceAllocation.resource_pool_id,
                ResourceAllocation.device_id,
            )
            .join(JobInstance, JobInstance.id == ResourceAllocation.job_instance_id)
            .where(
                ResourceAllocation.resource_pool_id.in_(pool_ids),
                JobInstance.status.in_(ACTIVE_JOB_STATUSES),
            )
        )
    ).all()
    for pool_id, device_id in alloc_rows:
        devices_by_pool.setdefault(pool_id, set()).add(device_id)

    return {pid: len(devs) for pid, devs in devices_by_pool.items()}


async def _device_existing_pools(
    db: AsyncSession,
    device_ids: list[int],
) -> Dict[int, set[int]]:
    """Return already-allocated pool_ids per device (active jobs + active sessions)."""
    if not device_ids:
        return {}

    rows = (
        await db.execute(
            select(
                ResourceAllocation.device_id,
                ResourceAllocation.resource_pool_id,
            )
            .join(JobInstance, JobInstance.id == ResourceAllocation.job_instance_id)
            .where(
                ResourceAllocation.device_id.in_(device_ids),
                JobInstance.status.in_(ACTIVE_JOB_STATUSES),
            )
        )
    ).all()

    session_rows = (
        await db.execute(
            select(
                DeviceNetworkSession.device_id,
                DeviceNetworkSession.resource_pool_id,
            ).where(
                DeviceNetworkSession.device_id.in_(device_ids),
                DeviceNetworkSession.status.in_(ACTIVE_NETWORK_SESSION_STATUSES),
            )
        )
    ).all()

    existing: dict[int, set[int]] = {}
    for device_id, pool_id in (*rows, *session_rows):
        existing.setdefault(device_id, set()).add(pool_id)
    return existing


async def allocate_devices(
    db: AsyncSession,
    device_ids: list[int],
    resource_type: str = "wifi",
    egress: str | None = None,
) -> dict[int, tuple[ResourcePool, dict[str, Any]]]:
    """Assign each device to the least-loaded active pool matching type & egress."""
    pools = await list_active_pools(db, resource_type=resource_type)
    if egress is not None:
        pools = [p for p in pools if pool_egress(p.config) == egress]
    if not pools:
        raise AllocationError(f"No active {resource_type} resource pools")

    pool_ids = [p.id for p in pools]
    loads = await _current_pool_loads(db, pool_ids)
    allocations: dict[int, tuple[ResourcePool, dict[str, Any]]] = {}

    for device_id in device_ids:
        ordered = sorted(
            pools,
            key=lambda p: (loads.get(p.id, 0), p.id),
        )

        chosen = None
        for pool in ordered:
            current_load = loads.get(pool.id, 0)
            if current_load >= pool.max_concurrent_devices:
                continue
            chosen = pool
            break

        if chosen is None:
            raise AllocationError(
                f"No capacity for device {device_id}: all pools full "
                f"({len(pools)} pools, max total {sum(p.max_concurrent_devices for p in pools)})"
            )

        loads[chosen.id] = loads.get(chosen.id, 0) + 1
        config = chosen.config or {}
        allocated_params = {
            "ssid": config.get("ssid", ""),
            "password": config.get("password", ""),
            "pool_name": chosen.name,
            "pool_id": chosen.id,
            "resource_type": chosen.resource_type,
            "egress": pool_egress(config),
        }
        allocations[device_id] = (chosen, allocated_params)

    logger.info(
        "resource_pool_allocate: type=%s devices=%d pools=%d",
        resource_type, len(device_ids), len(pools),
    )
    return allocations


async def create_allocations(
    db: AsyncSession,
    assignments: dict[int, tuple[ResourcePool, dict[str, Any]]],
    job_device_map: dict[int, int],
) -> list[ResourceAllocation]:
    """Persist ResourceAllocation records."""
    records = []
    for job_id, device_id in job_device_map.items():
        if device_id not in assignments:
            continue
        _pool, params = assignments[device_id]
        records.append(
            ResourceAllocation(
                job_instance_id=job_id,
                resource_pool_id=_pool.id,
                device_id=device_id,
                allocated_params=params,
            )
        )

    if records:
        db.add_all(records)
        await db.flush()

    return records


async def get_pool_load_summary(
    db: AsyncSession,
    resource_type: str | None = None,
) -> list[dict[str, Any]]:
    """Return load summary for all active pools (both ``wifi`` and ``usb_net``)."""
    pools = await list_active_pools(db, resource_type=resource_type)
    if not pools:
        return []

    pool_ids = [p.id for p in pools]
    loads = await _current_pool_loads(db, pool_ids)

    return [
        {
            "id": p.id,
            "name": p.name,
            "resource_type": p.resource_type,
            "max_concurrent_devices": p.max_concurrent_devices,
            "current_devices": loads.get(p.id, 0),
            "host_group": p.host_group,
            "is_active": p.is_active,
            "config": public_pool_config(p.config),
        }
        for p in pools
    ]
