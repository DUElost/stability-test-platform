# -*- coding: utf-8 -*-
"""Audit logging helper for tracking mutation operations."""

import logging
from typing import Any, Dict, Optional

from fastapi import Request
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.orm.session import object_session

from backend.core.limiter import resolve_client_ip
from backend.models.audit import AuditLog

logger = logging.getLogger(__name__)

#: #2778：审计 `resource_type` 的**唯一权威 = 实体对应的表名**。无独立表的实体
#: （会话、资源池类型、死信队列、任务/定时等派生面）在此显式登记，登记即视为规范值。
#: 写侧只允许使用本集合内的值；新增资源类型必须先登记（机械守卫见
#: `backend/tests/test_audit_resource_type_guard.py`）。
AUDIT_RESOURCE_TYPES: frozenset[str] = frozenset({
    "agent_dead_letter",
    "ai_assistant_action",
    "ai_assistant_config",
    "ai_chat_session",
    "audit_log",
    "device",
    "host",
    "jira_run",
    "job_instance",
    "notification_channel",
    "notification_rule",
    "plan",
    "plan_run",
    "resource_pool",
    "schedule",
    "script",
    "session",
    "task",
    "test_case",
    "test_project",
    "test_suite",
    "user",
    "wifi",
})

#: #2778：历史别名 → 规范值。审计行 append-only、不可追溯改写（ADR-0015），
#: 历史字面量只在**读取侧**归并（列表过滤展开 + facets 合并计数）；写侧禁止再产出别名。
AUDIT_RESOURCE_TYPE_ALIASES: dict[str, str] = {
    # job_terminalized 曾按收尾路径分裂：Agent 完成侧写 job、回收器侧写 job_instance（#2778）
    "job": "job_instance",
    # 脚本目录扫描曾写 script_catalog，规范实体是 script 表（#2778）
    "script_catalog": "script",
}


def canonical_resource_type(value: str) -> str:
    """把历史别名归一到规范 `resource_type`；未登记值原样返回（不吞错）。"""
    return AUDIT_RESOURCE_TYPE_ALIASES.get(value, value)


def expand_resource_type_filter(value: str) -> list[str]:
    """筛选值 → 需匹配的全部字面量（规范值 + 其历史别名），保证历史行仍筛得到。

    例：``job_instance`` → ``["job", "job_instance"]``；未登记值 → ``[value]``。
    """
    canonical = canonical_resource_type(value)
    aliases = [
        alias for alias, target in AUDIT_RESOURCE_TYPE_ALIASES.items()
        if target == canonical
    ]
    return sorted({canonical, *aliases})


def _audit_client_ip(request: Optional[Request]) -> Optional[str]:
    """审计 IP(#281 CR Major):只读受信任代理边界规范化后的结果。

    直接解析 ``X-Forwarded-For`` 头会被客户端伪造——nginx 的
    ``$proxy_add_x_forwarded_for`` 会把客户端自带的 XFF 拼在链首,
    取最左侧即攻击者可控值。复用 limiter 的可信代理解析:对端不可信
    时完全忽略 XFF;对端可信时从右往左取第一个非可信条目。
    """
    if request is None:
        return None
    return resolve_client_ip(
        request.client.host if request.client else None,
        request.headers.get("X-Forwarded-For"),
    )


def record_audit(
    db: Session,
    *,
    action: str,
    resource_type: str,
    resource_id: Optional[Any] = None,
    details: Optional[Dict[str, Any]] = None,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    request: Optional[Request] = None,
    strict: bool = False,
) -> Optional[AuditLog]:
    """Record an audit log entry for a mutation operation.

    ``strict=True``：审计表缺失时**不再降级跳过**，直接抛出——供「审计是事件
    真源、写不进去就不许改状态」的 fail-closed 路径使用（ADR-0038 D2 退役，
    #1801）。默认 False 保持既有容错语义（缺表仅告警）。
    """
    ip_address = _audit_client_ip(request)

    # AuditLog.resource_id 是 String(64),需把整型主键(job_id / plan_run_id / ...)
    # 转字符串后再入库;PG 严格类型不会做隐式 int→varchar 转换。
    resource_id_str = None if resource_id is None else str(resource_id)
    entry = AuditLog(
        user_id=user_id,
        username=username,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id_str,
        details=details or {},
        ip_address=ip_address,
    )
    try:
        # 使用 savepoint 包裹审计写入，避免审计失败污染主业务事务
        with db.begin_nested():
            db.add(entry)
            db.flush()
    except (ProgrammingError, OperationalError) as exc:
        message = str(exc)
        is_missing_audit_table = (
            "audit_logs" in message
            and (
                "does not exist" in message.lower()
                or "undefinedtable" in message.lower()
                or "不存在" in message
            )
        )
        if not is_missing_audit_table or strict:
            raise

        # 缺少 audit_logs 表时降级：仅记录告警，不阻塞主流程
        if object_session(entry) is db:
            db.expunge(entry)
        logger.warning(
            "audit_logs_missing_skip: %s %s/%s by %s",
            action,
            resource_type,
            resource_id,
            username or user_id or "anonymous",
        )
        return None

    logger.info(
        "audit: %s %s/%s by %s",
        action,
        resource_type,
        resource_id,
        username or user_id or "anonymous",
    )
    return entry


async def record_audit_async(
    db: AsyncSession,
    *,
    action: str,
    resource_type: str,
    resource_id: Optional[Any] = None,
    details: Optional[Dict[str, Any]] = None,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    request: Optional[Request] = None,
) -> Optional[AuditLog]:
    """Record an audit entry for routes backed by AsyncSession."""
    ip_address = _audit_client_ip(request)

    resource_id_str = None if resource_id is None else str(resource_id)
    entry = AuditLog(
        user_id=user_id,
        username=username,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id_str,
        details=details or {},
        ip_address=ip_address,
    )
    try:
        async with db.begin_nested():
            db.add(entry)
            await db.flush()
    except (ProgrammingError, OperationalError) as exc:
        message = str(exc)
        is_missing_audit_table = (
            "audit_logs" in message
            and (
                "does not exist" in message.lower()
                or "undefinedtable" in message.lower()
                or "不存在" in message
            )
        )
        if not is_missing_audit_table:
            raise

        if object_session(entry) is db.sync_session:
            db.sync_session.expunge(entry)
        logger.warning(
            "audit_logs_missing_skip: %s %s/%s by %s",
            action,
            resource_type,
            resource_id,
            username or user_id or "anonymous",
        )
        return None

    logger.info(
        "audit: %s %s/%s by %s",
        action,
        resource_type,
        resource_id,
        username or user_id or "anonymous",
    )
    return entry
