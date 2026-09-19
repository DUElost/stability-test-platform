# -*- coding: utf-8 -*-
"""
Audit Log API — admin-only read access to audit trail.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.routes.auth import require_admin, User
from backend.api.schemas import (
    AuditFacetValue,
    AuditFacetsOut,
    AuditLogOut,
    PaginatedResponse,
)
from backend.core.audit import canonical_resource_type, expand_resource_type_filter
from backend.core.database import get_async_db
from backend.models.audit import AuditLog

router = APIRouter(prefix="/api/v1/audit-logs", tags=["audit"])

#: #2694：facets 每维最多返回的候选数（按条数倒序取 top-N）。
#: 定 50 的依据：`action` 实测 86+ 种且持续增长（无界），而**筛选下拉的可用性**在
#: 几十项之后就饱和——长尾用 datalist 精确输入即可（#628 同范式）。取 50 而非更小值，
#: 是为了在同一个下拉里仍能容纳「全部业务类动作」而不被高频噪声项挤掉。
_FACET_LIMIT = 50


def _is_missing_audit_table(exc: ProgrammingError) -> bool:
    """环境尚未创建 `audit_logs`（老库/未跑迁移）时的判定。

    调用方据此返回空结果而不是 500——列表与 facets 共用同一条兜底口径，
    否则「表没建」时一边能用一边 500，比统一报错更难解释。
    """
    message = str(exc)
    return "audit_logs" in message and (
        "does not exist" in message.lower()
        or "undefinedtable" in message.lower()
        or "不存在" in message
    )


def _apply_audit_filters(
    stmt,
    *,
    resource_type: Optional[str],
    action: Optional[str],
    user_id: Optional[int],
    username: Optional[str],
    ip_address: Optional[str],
    resource_id: Optional[str],
    start_time: Optional[datetime],
    end_time: Optional[datetime],
):
    if resource_type:
        # #2778：筛选按**规范值 + 其历史别名**展开——审计行不改写（ADR-0015），
        # 但「资源=job_instance」必须同时命中历史 job 行，否则静默丢一半。
        stmt = stmt.where(
            AuditLog.resource_type.in_(expand_resource_type_filter(resource_type))
        )
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)
    # #628：审计行自带 username/ip_address 快照（不 join users），按原值精确匹配；
    # resource_id 是 varchar（#832：与业务主键类型无关），同样按字符串精确匹配。
    if username:
        stmt = stmt.where(AuditLog.username == username)
    if ip_address:
        stmt = stmt.where(AuditLog.ip_address == ip_address)
    if resource_id:
        stmt = stmt.where(AuditLog.resource_id == resource_id)
    if start_time:
        stmt = stmt.where(AuditLog.timestamp >= start_time)
    if end_time:
        stmt = stmt.where(AuditLog.timestamp <= end_time)
    return stmt


def _merge_alias_facets(
    entries: List[AuditFacetValue], limit: int
) -> List[AuditFacetValue]:
    """#2778：把历史别名并入规范值（计数求和）后按计数重排。

    审计行不可追溯改写，历史字面量（如 `job`）会长期在库里；归并只发生在读取侧，
    使下拉里同一实体只出现一项——否则管理员按收尾路径不同会看到两个"半真选项"，
    筛一个就静默丢掉另一个的存量。
    """
    merged: dict = {}
    for entry in entries:
        canonical = canonical_resource_type(entry.value)
        merged[canonical] = merged.get(canonical, 0) + entry.count
    ordered = sorted(merged.items(), key=lambda kv: (-kv[1], kv[0]))
    return [AuditFacetValue(value=value, count=count) for value, count in ordered[:limit]]


@router.get("", response_model=PaginatedResponse)
async def list_audit_logs(
    resource_type: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    user_id: Optional[int] = Query(None),
    username: Optional[str] = Query(None),
    ip_address: Optional[str] = Query(None),
    resource_id: Optional[str] = Query(None),
    start_time: Optional[datetime] = Query(None),
    end_time: Optional[datetime] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_async_db),
    _current_user: User = Depends(require_admin),
):
    """List audit log entries (admin-only, paginated)."""
    base = _apply_audit_filters(
        select(AuditLog),
        resource_type=resource_type,
        action=action,
        user_id=user_id,
        username=username,
        ip_address=ip_address,
        resource_id=resource_id,
        start_time=start_time,
        end_time=end_time,
    )

    try:
        total = int(
            (
                await db.execute(
                    select(func.count()).select_from(base.subquery())
                )
            ).scalar_one()
        )
        rows = (
            await db.execute(
                base.order_by(AuditLog.timestamp.desc()).offset(skip).limit(limit)
            )
        ).scalars().all()
    except ProgrammingError as exc:
        # 兼容尚未创建 audit_logs 的环境：返回空结果而不是 500
        if not _is_missing_audit_table(exc):
            raise
        await db.rollback()
        return PaginatedResponse(items=[], total=0, skip=skip, limit=limit)

    items = [AuditLogOut.model_validate(r) for r in rows]
    return PaginatedResponse(items=items, total=total, skip=skip, limit=limit)


@router.get("/facets", response_model=AuditFacetsOut)
async def get_audit_filter_facets(
    db: AsyncSession = Depends(get_async_db),
    _current_user: User = Depends(require_admin),
):
    """审计筛选候选（#2629）：值域 = 表里**真实写入过**的 distinct 值。

    「筛选选项」与「写入词表」原先各写各的（后端裸 `str` + 精确等值，前端 9 资源/6 操作
    硬编码），于是 6 个死选项给出**自信的假阴性**、98.5% 的记录没有入口，而且写入侧改名
    不会有任何测试报警。本端点把这两侧收成一个来源：**能选出来的一定筛得出东西**。

    两个维度都按条数倒序（高频合规关注点排在前面）；`action` 有 86 种字面量，前端因此
    用 datalist + 精确匹配（与 #628 的用户名/IP 同范式），而不是假装能列全。

    #2778：资源维在 distinct 之上再按**规范值**归并历史别名（写侧已统一，见
    `backend/core/audit.py` 的词表），使下拉与列表过滤口径一致；`action` 维不归并。
    """
    async def _facet_values(column, limit: int) -> List[AuditFacetValue]:
        """取该维度的 top-N distinct 值（按条数倒序）。

        #2694：加 `limit` 后结果为**有界**——原先无界返回全部 distinct 值。生产实测该表
        已 **266,882 行**、`action` 86+ 种，每次开 `/audit` 都把整个聚合结果搬给前端
        datalist；有界后传输与前端渲染有界，下拉也不再被单一高频项（实测
        `job_terminalized` 占近 30 天 76%）挤出可视范围。

        取 **top-N** 而非「全量交给前端截断」：审计筛选的候选集本就是「管理员大概率要找的
        那几个」，长尾用 datalist 精确输入（#628 同范式，与本文档既有说明一致）。
        """
        stmt = (
            select(column, func.count())
            .where(column.is_not(None))
            .group_by(column)
            .order_by(func.count().desc(), column.asc())
            .limit(limit)
        )
        rows = (await db.execute(stmt)).all()
        return [AuditFacetValue(value=str(value), count=int(count or 0))
                for value, count in rows]

    try:
        return AuditFacetsOut(
            # #2778：资源维按规范值归并（job→job_instance、script_catalog→script），
            # 保证下拉与列表过滤同一口径；action 维无别名概念，原样透传。
            resource_types=_merge_alias_facets(
                await _facet_values(AuditLog.resource_type, _FACET_LIMIT),
                _FACET_LIMIT,
            ),
            actions=await _facet_values(AuditLog.action, _FACET_LIMIT),
        )
    except ProgrammingError as exc:
        if not _is_missing_audit_table(exc):
            raise
        await db.rollback()
        return AuditFacetsOut()
