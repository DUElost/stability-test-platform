"""Agent network-supply grant renewal route (ADR-0066 D5.3 & D6.3 / B13 U1).

Split from ``agent_api.py`` to keep that god-file under the line ceiling (#736).
Mounted under the same ``/api/v1/agent`` prefix.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import verify_agent_secret as _verify_agent
from backend.core.database import get_db
from backend.services.network_return import (
    ack_usbnet_revoke_sync,
    report_manual_network_release_sync,
)
from backend.services.network_supply import renew_usbnet_grant_sync

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


class UsbnetGrantRenewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host_id: str = Field(..., min_length=1, max_length=64)
    generation: int = Field(..., ge=1)
    usb_devpath: str = Field(..., min_length=1, max_length=128)
    min_allowed_iat_by_egress: Optional[Dict[str, int]] = None


class UsbnetGrantRenewOut(BaseModel):
    session_id: int
    generation: int
    host_id: str
    device_serial: str
    usb_devpath: str
    egress: str
    iat: int
    exp: int
    usbnet_grant: str


@router.post(
    "/network-sessions/{session_id}/grant-renew",
    response_model=ApiResponse[UsbnetGrantRenewOut],
)
def agent_renew_usbnet_grant(
    session_id: int,
    payload: UsbnetGrantRenewIn,
    db: Session = Depends(get_db),
    _=Depends(_verify_agent),
):
    """Renew short-lived Ed25519 ``usbnet_grant`` for an active ``usb_net`` session."""
    result = renew_usbnet_grant_sync(
        db,
        session_id=session_id,
        host_id=payload.host_id,
        generation=payload.generation,
        usb_devpath=payload.usb_devpath,
        min_allowed_iat_by_egress=payload.min_allowed_iat_by_egress,
    )
    return ok(UsbnetGrantRenewOut(**result))


class ManualNetworkReleaseReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host_id: str = Field(..., min_length=1, max_length=64)
    generation: int = Field(..., ge=1)
    confirmed: bool
    evidence: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = Field(default=None, max_length=512)


class UsbnetRevokeAckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host_id: str = Field(..., min_length=1, max_length=64)
    generation: int = Field(..., ge=1)


@router.post(
    "/network-sessions/{session_id}/release-report",
    response_model=ApiResponse[Dict[str, Any]],
)
def agent_report_manual_network_release(
    session_id: int,
    payload: ManualNetworkReleaseReportIn,
    db: Session = Depends(get_db),
    _=Depends(_verify_agent),
):
    """Agent callback reporting manual wifi/usb_net release confirmation or failure."""
    return ok(
        report_manual_network_release_sync(
            db,
            session_id=session_id,
            host_id=payload.host_id,
            generation=payload.generation,
            confirmed=payload.confirmed,
            evidence=payload.evidence,
            error_message=payload.error_message,
        )
    )


@router.post(
    "/network-sessions/{session_id}/revoke-ack",
    response_model=ApiResponse[Dict[str, Any]],
)
def agent_ack_usbnet_revoke(
    session_id: int,
    payload: UsbnetRevokeAckIn,
    db: Session = Depends(get_db),
    _=Depends(_verify_agent),
):
    """Agent acknowledges ``stp-agent-priv usbnet-revoke`` execution after override."""
    return ok(
        ack_usbnet_revoke_sync(
            db,
            session_id=session_id,
            host_id=payload.host_id,
            generation=payload.generation,
        )
    )
