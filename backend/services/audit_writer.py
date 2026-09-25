"""审计记录写入（#3297 自 core/audit.py 下沉，C1「S4 出口」）。

为什么在 services 而不是继续留 core：写入要 import `backend.models.audit`，
而 C1 分层里 core 是最底层、不得上引 models（原 `core.audit → models.audit`
基线行的登记出口就是「审计记录写入下沉到 services 或 models 侧」）。

为什么也不拆一份「Request 依赖部分放 api 层」：`record_audit` 的调用方横跨
api.routes / services / scheduler / scripts 四个域（services 与 scheduler 够不着
api 层），把 request→ip 的提取单独放 api 会让这些调用点无处安放。因此本模块对
request 只做**结构化取用**（读 `.client.host` 与 `.headers`，零 fastapi/starlette
import）——C4 要消除的是 core→web 框架依赖，本模块满足；未来 #3295 的 C2 棘轮
若把 services→fastapi 清零，也不欠本模块任何东西（它不 import fastapi）。
"""

import logging
from typing import Any, Dict, Optional

from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.orm.session import object_session

from backend.core.limiter import resolve_client_ip
from backend.models.audit import AuditLog

logger = logging.getLogger(__name__)


def _audit_client_ip(request: Optional[Any]) -> Optional[str]:
    """审计 IP(#281 CR Major):只读受信任代理边界规范化后的结果。

    直接解析 ``X-Forwarded-For`` 头会被客户端伪造——nginx 的
    ``$proxy_add_x_forwarded_for`` 会把客户端自带的 XFF 拼在链首,
    取最左侧即攻击者可控值。复用 core.limiter 的可信代理解析:对端不可信
    时完全忽略 XFF;对端可信时从右往左取第一个非可信条目。

    ``request`` 结构化取用（starlette Request 天然满足），不 import web 框架。
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
    request: Optional[Any] = None,
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
    request: Optional[Any] = None,
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
