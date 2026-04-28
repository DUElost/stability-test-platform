"""Resource pool CRUD + allocation status."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.api.response import ApiResponse
from backend.core.database import get_async_db
from backend.models.resource_pool import ResourceAllocation, ResourcePool
from backend.services.resource_pool import get_pool_load_summary

router = APIRouter(prefix="/api/v1/resource-pools", tags=["resource-pools"])


class ResourcePoolIn(BaseModel):
    name: str = Field(..., max_length=256)
    resource_type: str = Field(default="wifi", max_length=32)
    config: Dict[str, Any] = Field(default_factory=dict)
    max_concurrent_devices: int = Field(default=30, ge=1, le=1000)
    host_group: Optional[str] = Field(default=None, max_length=128)
    is_active: bool = True


class ResourcePoolOut(BaseModel):
    id: int
    name: str
    resource_type: str
    config: Dict[str, Any]
    max_concurrent_devices: int
    host_group: Optional[str]
    is_active: bool

    class Config:
        from_attributes = True


class ResourcePoolLoad(BaseModel):
    id: int
    name: str
    resource_type: str
    max_concurrent_devices: int
    current_devices: int
    host_group: Optional[str]
    is_active: bool


@router.get("")
async def list_pools(
    resource_type: Optional[str] = None,
    db: AsyncSession = Depends(get_async_db),
):
    clauses = []
    if resource_type:
        clauses.append(ResourcePool.resource_type == resource_type)
    result = await db.execute(
        select(ResourcePool).where(*clauses).order_by(ResourcePool.id)
    )
    pools = result.scalars().all()
    return ApiResponse(data=[ResourcePoolOut.model_validate(p) for p in pools])


@router.get("/loads")
async def pool_loads(db: AsyncSession = Depends(get_async_db)):
    summary = await get_pool_load_summary(db)
    return ApiResponse(data=[ResourcePoolLoad(**s) for s in summary])


@router.get("/{pool_id}")
async def get_pool(pool_id: int, db: AsyncSession = Depends(get_async_db)):
    pool = await db.get(ResourcePool, pool_id)
    if not pool:
        raise HTTPException(status_code=404, detail="Resource pool not found")
    return ApiResponse(data=ResourcePoolOut.model_validate(pool))


@router.post("", status_code=201)
async def create_pool(body: ResourcePoolIn, db: AsyncSession = Depends(get_async_db)):
    pool = ResourcePool(
        name=body.name,
        resource_type=body.resource_type,
        config=body.config,
        max_concurrent_devices=body.max_concurrent_devices,
        host_group=body.host_group,
        is_active=body.is_active,
    )
    db.add(pool)
    await db.commit()
    await db.refresh(pool)
    return ApiResponse(data=ResourcePoolOut.model_validate(pool))


@router.put("/{pool_id}")
async def update_pool(pool_id: int, body: ResourcePoolIn, db: AsyncSession = Depends(get_async_db)):
    pool = await db.get(ResourcePool, pool_id)
    if not pool:
        raise HTTPException(status_code=404, detail="Resource pool not found")

    pool.name = body.name
    pool.resource_type = body.resource_type
    pool.config = body.config
    pool.max_concurrent_devices = body.max_concurrent_devices
    pool.host_group = body.host_group
    pool.is_active = body.is_active
    await db.commit()
    await db.refresh(pool)
    return ApiResponse(data=ResourcePoolOut.model_validate(pool))


@router.delete("/{pool_id}", status_code=204)
async def delete_pool(pool_id: int, db: AsyncSession = Depends(get_async_db)):
    pool = await db.get(ResourcePool, pool_id)
    if not pool:
        raise HTTPException(status_code=404, detail="Resource pool not found")
    await db.delete(pool)
    await db.commit()
