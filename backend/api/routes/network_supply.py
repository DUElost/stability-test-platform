"""Admin network-supply session routes (ADR-0066 D2.5–D2.6 / B13 U1)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import User, require_admin
from backend.core.database import get_db
from backend.services.network_return import (
    audit_override_network_session_sync,
    request_manual_network_release_sync,
)
from backend.services.network_supply import (
    enable_manual_network_supply_sync,
    list_network_sessions_sync,
    renew_manual_network_session_sync,
)

router = APIRouter(prefix="/api/v1/network-supply", tags=["network-supply"])


class ManualNetworkEnableIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: int = Field(..., gt=0)
    resource_pool_id: int = Field(..., gt=0)
    ttl_seconds: Optional[int] = Field(default=None, ge=300)


class ManualNetworkRenewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extend_seconds: int = Field(default=3600, ge=300)


class NetworkSessionAuditOverrideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_category: str = Field(..., min_length=1, max_length=64)
    evidence_ref: str = Field(..., min_length=1, max_length=512)


@router.get("/sessions", response_model=ApiResponse[list[dict[str, Any]]])
def list_network_sessions(
    include_released: bool = Query(default=False),
    host_id: Optional[str] = Query(default=None),
    resource_type: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """List managed ``device_network_session`` rows (admin only)."""
    return ok(
        list_network_sessions_sync(
            db,
            include_released=include_released,
            host_id=host_id,
            resource_type=resource_type,
            source=source,
            status=status,
        )
    )


@router.post("/manual/enable", response_model=ApiResponse[dict[str, Any]], status_code=201)
def enable_manual_network_supply(
    body: ManualNetworkEnableIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Atomically enable manual debug network supply on an idle device."""
    return ok(
        enable_manual_network_supply_sync(
            db,
            device_id=body.device_id,
            resource_pool_id=body.resource_pool_id,
            admin_username=current_user.username,
            admin_user_id=current_user.id,
            ttl_seconds=body.ttl_seconds,
            request=request,
        )
    )


@router.post("/manual/{session_id}/renew", response_model=ApiResponse[dict[str, Any]])
def renew_manual_network_session(
    session_id: int,
    body: ManualNetworkRenewIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Extend ``expires_at`` on an active manual network-supply session."""
    return ok(
        renew_manual_network_session_sync(
            db,
            session_id=session_id,
            extend_seconds=body.extend_seconds,
            admin_username=current_user.username,
            admin_user_id=current_user.id,
            request=request,
        )
    )


@router.post("/sessions/{session_id}/release", response_model=ApiResponse[dict[str, Any]])
def request_manual_network_release(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Initiate force release on a manual network-supply session (marks RELEASING)."""
    return ok(
        request_manual_network_release_sync(
            db,
            session_id=session_id,
            admin_username=current_user.username,
            admin_user_id=current_user.id,
            request=request,
        )
    )


@router.post(
    "/sessions/{session_id}/audit-override",
    response_model=ApiResponse[dict[str, Any]],
)
def audit_override_network_session(
    session_id: int,
    body: NetworkSessionAuditOverrideIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Dedicated admin audit-override closing session, usb_net debt & eligible lease."""
    return ok(
        audit_override_network_session_sync(
            db,
            session_id=session_id,
            reason_category=body.reason_category,
            evidence_ref=body.evidence_ref,
            admin_username=current_user.username,
            admin_user_id=current_user.id,
            request=request,
        )
    )
