"""Resource pool CRUD + allocation status."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.response import ApiResponse
from backend.api.routes.auth import get_current_active_user, require_admin, User
from backend.core.audit import record_audit_async
from backend.core.database import get_async_db
from backend.models.resource_pool import ResourceAllocation, ResourcePool
from backend.services.resource_pool import get_pool_load_summary

router = APIRouter(prefix="/api/v1/resource-pools", tags=["resource-pools"])

# #955: 普通用户可选的池列表不得携带凭据。白名单只放非机密展示字段——
# 保守方向：白名单外的新增 config 键默认不返回（防未来机密字段泄漏）。
_PUBLIC_CONFIG_KEYS = ("ssid", "band", "router_ip", "mac_filter")


class ResourcePoolIn(BaseModel):
    name: str = Field(..., max_length=256)
    resource_type: str = Field(default="wifi", max_length=32)
    config: Dict[str, Any] = Field(default_factory=dict)
    max_concurrent_devices: int = Field(default=30, ge=1, le=1000)
    host_group: Optional[str] = Field(default=None, max_length=128)
    is_active: bool = True


class ResourcePoolOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    resource_type: str
    config: Dict[str, Any]
    max_concurrent_devices: int
    host_group: Optional[str]
    is_active: bool


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
    _current_user: User = Depends(require_admin),
):
    clauses = []
    if resource_type:
        clauses.append(ResourcePool.resource_type == resource_type)
    result = await db.execute(
        select(ResourcePool).where(*clauses).order_by(ResourcePool.id)
    )
    pools = result.scalars().all()
    return ApiResponse(data=[ResourcePoolOut.model_validate(p) for p in pools])


@router.get("/available")
async def list_available_pools(
    resource_type: Optional[str] = None,
    db: AsyncSession = Depends(get_async_db),
    _current_user: User = Depends(get_current_active_user),
):
    """#955: 普通登录用户可选池列表——只含 active 池，config 白名单剥密。

    管理接口（GET /resource-pools）保持 admin + 完整 config（含凭据）；
    Plan 执行的 WiFi 选择器用本端点，不泄漏 password 等机密。
    """
    clauses = [ResourcePool.is_active.is_(True)]
    if resource_type:
        clauses.append(ResourcePool.resource_type == resource_type)
    result = await db.execute(
        select(ResourcePool).where(*clauses).order_by(ResourcePool.id)
    )
    pools = result.scalars().all()
    return ApiResponse(data=[
        ResourcePoolOut(
            id=p.id,
            name=p.name,
            resource_type=p.resource_type,
            config={
                key: value
                for key, value in (p.config or {}).items()
                if key in _PUBLIC_CONFIG_KEYS
            },
            max_concurrent_devices=p.max_concurrent_devices,
            host_group=p.host_group,
            is_active=p.is_active,
        )
        for p in pools
    ])


@router.get("/loads")
async def pool_loads(
    db: AsyncSession = Depends(get_async_db),
    _current_user: User = Depends(require_admin),
):
    summary = await get_pool_load_summary(db)
    return ApiResponse(data=[ResourcePoolLoad(**s) for s in summary])


@router.get("/{pool_id}")
async def get_pool(
    pool_id: int,
    db: AsyncSession = Depends(get_async_db),
    _current_user: User = Depends(require_admin),
):
    pool = await db.get(ResourcePool, pool_id)
    if not pool:
        raise HTTPException(status_code=404, detail="Resource pool not found")
    return ApiResponse(data=ResourcePoolOut.model_validate(pool))


@router.post("", status_code=201)
async def create_pool(
    body: ResourcePoolIn,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_admin),
    request: Request = None,
):
    pool = ResourcePool(
        name=body.name,
        resource_type=body.resource_type,
        config=body.config,
        max_concurrent_devices=body.max_concurrent_devices,
        host_group=body.host_group,
        is_active=body.is_active,
    )
    db.add(pool)
    await db.flush()
    await record_audit_async(
        db,
        action="create",
        resource_type="resource_pool",
        resource_id=pool.id,
        details={"name": pool.name, "resource_type": pool.resource_type, "host_group": pool.host_group},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    await db.commit()
    await db.refresh(pool)
    return ApiResponse(data=ResourcePoolOut.model_validate(pool))


@router.put("/{pool_id}")
async def update_pool(
    pool_id: int,
    body: ResourcePoolIn,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_admin),
    request: Request = None,
):
    pool = await db.get(ResourcePool, pool_id)
    if not pool:
        raise HTTPException(status_code=404, detail="Resource pool not found")

    pool.name = body.name
    pool.resource_type = body.resource_type
    pool.config = body.config
    pool.max_concurrent_devices = body.max_concurrent_devices
    pool.host_group = body.host_group
    pool.is_active = body.is_active
    await record_audit_async(
        db,
        action="update",
        resource_type="resource_pool",
        resource_id=pool.id,
        details={"name": pool.name, "resource_type": pool.resource_type, "host_group": pool.host_group},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    await db.commit()
    await db.refresh(pool)
    return ApiResponse(data=ResourcePoolOut.model_validate(pool))


@router.delete("/{pool_id}", status_code=204)
async def delete_pool(
    pool_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(require_admin),
    request: Request = None,
):
    pool = await db.get(ResourcePool, pool_id)
    if not pool:
        raise HTTPException(status_code=404, detail="Resource pool not found")

    # #937: 仍有分配记录的资源池不可硬删除——resource_allocation.resource_pool_id
    # 为非空引用，裸删会 IntegrityError 500。
    alloc_count = (
        await db.execute(
            select(func.count(ResourceAllocation.id)).where(
                ResourceAllocation.resource_pool_id == pool_id,
            )
        )
    ).scalar() or 0
    if alloc_count:
        raise HTTPException(
            status_code=409,
            detail=f"资源池有 {alloc_count} 条分配记录，不可删除",
        )

    await record_audit_async(
        db,
        action="delete",
        resource_type="resource_pool",
        resource_id=pool.id,
        details={"name": pool.name, "resource_type": pool.resource_type, "host_group": pool.host_group},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    await db.delete(pool)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="资源池仍被其它记录引用，不可删除",
        ) from exc
