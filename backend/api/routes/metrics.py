"""
Metrics API Endpoint

Exposes Prometheus metrics at /metrics endpoint.
"""

from __future__ import annotations

import os
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status

from backend.core.agent_secret import AgentSecretNotConfiguredError, require_agent_secret
from backend.core.metrics import get_metrics_response, is_prometheus_available
from backend.core.security import decode_token

router = APIRouter()


def _metrics_auth_required() -> bool:
    return os.getenv("STP_METRICS_AUTH_REQUIRED", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def verify_metrics_access(
    authorization: Optional[str] = Header(None),
    x_agent_secret: Optional[str] = Header(None),
) -> None:
    """Optional Bearer access token or X-Agent-Secret when auth is enabled."""
    if not _metrics_auth_required():
        return

    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        if decode_token(token, expected_type="access"):
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
async def metrics(_auth: None = Depends(verify_metrics_access)):
    """
    Prometheus metrics endpoint.

    Returns metrics in Prometheus exposition format.
    """
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
