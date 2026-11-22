"""Agent 传输并发许可端点（ADR-0064 D3 / B10 U3）。

鉴权：``X-Agent-Secret``。第一期无前端 UI 消费方。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import verify_agent_secret as _verify_agent
from backend.api.schemas.transfer_permit import (
    TransferPermitAcquireIn,
    TransferPermitAcquireOut,
    TransferPermitReleaseIn,
    TransferPermitReleaseOut,
    TransferPermitRenewIn,
    TransferPermitRenewOut,
)
from backend.services.transfer_permit import (
    TransferPermitError,
    acquire_permit,
    release_permit,
    renew_permit,
)

router = APIRouter(prefix="/api/v1/agent/transfer-permits", tags=["agent-transfer-permits"])


def _raise_permit_http(exc: TransferPermitError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    ) from exc


@router.post("/acquire", response_model=ApiResponse[TransferPermitAcquireOut])
def acquire_transfer_permit(
    payload: TransferPermitAcquireIn,
    _=Depends(_verify_agent),
):
    """申请全局传输槽位；满员时返回 ``acquired=false``（HTTP 200）。"""
    try:
        result = acquire_permit(
            host_id=payload.host_id,
            lease_token=payload.lease_token,
            pool=payload.pool,
            ttl_seconds=payload.ttl_seconds,
        )
    except TransferPermitError as exc:
        _raise_permit_http(exc)
    return ok(
        TransferPermitAcquireOut(
            acquired=result.acquired,
            pool=result.pool,
            host_id=result.host_id,
            lease_token=result.lease_token,
            active_count=result.active_count,
            max_hosts=result.max_hosts,
            ttl_seconds=result.ttl_seconds,
        )
    )


@router.post("/renew", response_model=ApiResponse[TransferPermitRenewOut])
def renew_transfer_permit(
    payload: TransferPermitRenewIn,
    _=Depends(_verify_agent),
):
    """续期；lease_token/host_id 不匹配时 409。"""
    try:
        ttl = renew_permit(
            host_id=payload.host_id,
            lease_token=payload.lease_token,
            pool=payload.pool,
            ttl_seconds=payload.ttl_seconds,
        )
    except TransferPermitError as exc:
        _raise_permit_http(exc)
    return ok(
        TransferPermitRenewOut(
            renewed=True,
            pool=payload.pool,
            host_id=payload.host_id,
            lease_token=payload.lease_token,
            ttl_seconds=ttl,
        )
    )


@router.post("/release", response_model=ApiResponse[TransferPermitReleaseOut])
def release_transfer_permit(
    payload: TransferPermitReleaseIn,
    _=Depends(_verify_agent),
):
    """释放；lease_token/host_id 不匹配时 409（绝不误释）。"""
    try:
        release_permit(
            host_id=payload.host_id,
            lease_token=payload.lease_token,
            pool=payload.pool,
        )
    except TransferPermitError as exc:
        _raise_permit_http(exc)
    return ok(
        TransferPermitReleaseOut(
            released=True,
            pool=payload.pool,
            host_id=payload.host_id,
            lease_token=payload.lease_token,
        )
    )
