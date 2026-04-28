"""Resource pool allocation service.

WiFi load balancing: given N devices to dispatch, assign each to the
least-loaded active pool. Allocation is stored in resource_allocation
table and freed when the referencing JobInstance completes.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Sequence

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models.enums import JobStatus
from backend.models.job import JobInstance
from backend.models.resource_pool import ResourceAllocation, ResourcePool

logger = logging.getLogger(__name__)

ACTIVE_JOB_STATUSES = (JobStatus.PENDING.value, JobStatus.RUNNING.value)


class AllocationError(Exception):
    pass


async def list_active_pools(
    db: AsyncSession,
    resource_type: str = "wifi",
    host_group: str | None = None,
) -> Sequence[ResourcePool]:
    """Return all active pools, optionally filtered by host_group."""
    clauses = [
        ResourcePool.resource_type == resource_type,
        ResourcePool.is_active.is_(True),
    ]
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
    """Return active device count per pool from current allocations."""
    if not pool_ids:
        return {}

    rows = (
        await db.execute(
            select(
                ResourceAllocation.resource_pool_id,
                func.count(ResourceAllocation.id),
            )
            .join(JobInstance, JobInstance.id == ResourceAllocation.job_instance_id)
            .where(
                ResourceAllocation.resource_pool_id.in_(pool_ids),
                JobInstance.status.in_(ACTIVE_JOB_STATUSES),
            )
            .group_by(ResourceAllocation.resource_pool_id)
        )
    ).all()

    return {row[0]: row[1] for row in rows}


async def _device_existing_pools(
    db: AsyncSession,
    device_ids: list[int],
) -> Dict[int, set[int]]:
    """Return already-allocated pool_ids per device (active jobs only).

    Prevents double-allocating a device that already holds a WiFi pool.
    """
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

    existing: dict[int, set[int]] = {}
    for device_id, pool_id in rows:
        existing.setdefault(device_id, set()).add(pool_id)
    return existing


async def allocate_devices(
    db: AsyncSession,
    device_ids: list[int],
    resource_type: str = "wifi",
) -> dict[int, tuple[ResourcePool, dict[str, Any]]]:
    """Assign each device to the least-loaded active pool.

    Returns:
        {device_id: (ResourcePool, allocated_params)}
        allocated_params contains SSID + password for injection into pipeline.

    Raises AllocationError if no pools available or capacity exhausted.
    """
    pools = await list_active_pools(db, resource_type=resource_type)
    if not pools:
        raise AllocationError(f"No active {resource_type} resource pools")

    pool_ids = [p.id for p in pools]
    loads = await _current_pool_loads(db, pool_ids)
    existing = await _device_existing_pools(db, device_ids)

    pool_objs = {p.id: p for p in pools}
    allocations: dict[int, tuple[ResourcePool, dict[str, Any]]] = {}

    for device_id in device_ids:
        # Least-loaded pool first, skip pools already assigned to this device
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
    """Persist ResourceAllocation records.

    Args:
        assignments: {device_id: (ResourcePool, allocated_params)}
        job_device_map: {job_instance_id: device_id}
    """
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


async def get_pool_load_summary(db: AsyncSession) -> list[dict[str, Any]]:
    """Return load summary for all active pools."""
    pools = await list_active_pools(db)
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
        }
        for p in pools
    ]
