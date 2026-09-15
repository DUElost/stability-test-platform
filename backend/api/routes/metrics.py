"""
Metrics API Endpoint

Exposes Prometheus metrics at /metrics endpoint.
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import func, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.core.agent_secret import AgentSecretNotConfiguredError, require_agent_secret
from backend.core.database import get_db
from backend.core.metrics import (
    device_online,
    get_metrics_response,
    host_online,
    is_prometheus_available,
    record_db_lock_waiters,
)
from backend.models.enums import DeviceStatus, HostStatus
from backend.models.host import Device, Host
from backend.services.auth_session import authenticate_token

logger = logging.getLogger(__name__)

router = APIRouter()


# #1258：host/device 在线计数在 /metrics 拉取时现算（表小、低基数，无周期
# 任务的 staleness；label 用小写枚举值与仪表板 PromQL 对齐）。
# ADR-0038 D5：host 侧排除退役（退役 = 不再是容量）——`stability_host_online`
# 的 PromQL 语义变化：退役主机的 online/offline/... 计数归 0，range 查询会在
# 退役时刻出现台阶（历史序列保留旧值）；若有告警按 fleet 规模阈值判断，退役
# 会正常触发「规模下降」而非误报。
_FLEET_GAUGES = (
    (Host, host_online, HostStatus, Host.retired_at.is_(None)),
    (Device, device_online, DeviceStatus, None),
)


def _refresh_fleet_gauges(db: Session) -> None:
    if not is_prometheus_available():
        return
    try:
        for model, gauge, status_enum, extra_filter in _FLEET_GAUGES:
            query = db.query(model.status, func.count()).group_by(model.status)
            if extra_filter is not None:
                query = query.filter(extra_filter)
            counts = dict(query.all())
            for member in status_enum:
                gauge.labels(status=member.value.lower()).set(counts.get(member.value, 0))
    except SQLAlchemyError:
        # 观测面不因 DB 抖动整体 500：保留其余指标输出，仅跳过舰队计数。
        logger.warning("metrics_fleet_gauge_refresh_failed", exc_info=True)


_LOCK_WAIT_SQL = text(
    "SELECT count(*) AS waiters, "
    # 必须用 clock_timestamp()（真实当前时间）而不是 now()：now() 是**事务起始**
    # 时间，而抓取事务通常开在等待出现之前 → now() - query_start 会是负数，
    # 取 max 后被 clamp 成 0，指标恒 0（本单回归测试实测踩到）。
    "COALESCE(max(EXTRACT(EPOCH FROM (clock_timestamp() - query_start))), 0) "
    "AS max_wait_seconds "
    "FROM pg_stat_activity "
    # 限定本库：pg_stat_activity 是全实例视图，不过滤会把别的库的等待算进来。
    "WHERE wait_event_type = 'Lock' AND pid <> pg_backend_pid() "
    "AND datname = current_database()"
)


def _refresh_lock_wait_gauges(db: Session) -> None:
    """#2104：把「此刻有多少会话在等锁 / 等最久多久」同步到 Prometheus。

    为什么需要它：锁序修复（#1959/#1980/#1985/#2022）消掉了环路等待，但代价会转移到
    **普通等待**（清理事务持行锁期间热路径排队、反向亦然）——这类等待对
    ``stability_db_deadlock_total`` 不可见，只看死锁计数会得出「计数为 0 = 无代价」
    的错误结论。与舰队 gauge 同口径：拉取期现算（一条聚合，无周期任务 staleness），
    失败只跳过本组、不拖垮整次抓取；非 PG 方言（sqlite）直接跳过，因为
    ``pg_stat_activity`` 是 PG 专有视图。
    """
    if not is_prometheus_available():
        return
    try:
        bind = db.get_bind()
        if bind.dialect.name != "postgresql":
            return
        # **必须先清统计快照**：`pg_stat_activity` 属 `pg_stat_*` 视图族，PG 15+ 在
        # **同一事务内**读的是事务起始时的写时复制快照。本函数前面刚跑过舰队 gauge
        # 查询（同一个请求 session），若不清快照，本次抓取就看不到「本事务开始之后
        # 才出现的等待」——表现为采样恒偏低甚至恒 0（#2022 在
        # `pg_stat_database.deadlocks` 上踩过同一坑；本单的回归测试也正因此先红）。
        db.execute(text("SELECT pg_stat_clear_snapshot()"))
        waiters, max_wait_seconds = db.execute(_LOCK_WAIT_SQL).one()
    except SQLAlchemyError:
        logger.warning("metrics_lock_wait_gauge_refresh_failed", exc_info=True)
        return
    record_db_lock_waiters(int(waiters or 0), float(max_wait_seconds or 0.0))


def _metrics_auth_required() -> bool:
    return os.getenv("STP_METRICS_AUTH_REQUIRED", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def verify_metrics_access(
    authorization: Optional[str] = Header(None),
    x_agent_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> None:
    """Optional Bearer access token or X-Agent-Secret when auth is enabled.

    R02-D3（#903）：Bearer 分支走 auth_session 完整校验面（此前仅签名级
    decode——停用/删除用户的 token 到 exp 前全通）。"""
    if not _metrics_auth_required():
        return

    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        if authenticate_token(db, token, expected_type="access"):
            return

    if x_agent_secret:
        try:
            expected = require_agent_secret()
        except AgentSecretNotConfiguredError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        if secrets.compare_digest(x_agent_secret, expected):
            return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Metrics authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.get("/metrics")
async def metrics(
    db: Session = Depends(get_db),
    _auth: None = Depends(verify_metrics_access),
):
    """
    Prometheus metrics endpoint.

    Returns metrics in Prometheus exposition format.
    """
    _refresh_fleet_gauges(db)
    _refresh_lock_wait_gauges(db)
    data, content_type = get_metrics_response()
    return Response(content=data, media_type=content_type)


@router.get("/metrics/health")
async def metrics_health():
    """
    Metrics subsystem health check.

    Returns whether the Prometheus client library is available.
    The authoritative /health endpoint (with DB connectivity check) is in main.py.
    """
    return {
        "status": "healthy",
        "prometheus_available": is_prometheus_available()
    }
