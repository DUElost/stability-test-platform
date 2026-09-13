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
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.core.agent_secret import AgentSecretNotConfiguredError, require_agent_secret
from backend.core.database import get_db
from backend.core.metrics import (
    device_online,
    get_metrics_response,
    host_online,
    is_prometheus_available,
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
