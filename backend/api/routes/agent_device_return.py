"""Agent device-return discharge endpoint (#3723 U3 / ADR-0061 D5.5).

Split from ``agent_api`` to keep that god-file under the line ceiling (#736).
Mounted under the same ``/api/v1/agent`` prefix.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import verify_agent_secret as _verify_agent
from backend.api.schemas.device import (
    AgentReturnDischargeIn,
    AgentReturnDischargeOut,
)
from backend.core.database import get_db
from backend.services.device_return import apply_discharge_sync

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


@router.post(
    "/devices/{device_id}/return-discharge",
    response_model=ApiResponse[AgentReturnDischargeOut],
)
def agent_return_discharge(
    device_id: int,
    payload: AgentReturnDischargeIn,
    db: Session = Depends(get_db),
    _=Depends(_verify_agent),
):
    """ADR-0061 D5.5 / #3723 §3.0 第 8 条：Agent 清偿回报（欠账租约令牌）。"""
    result = apply_discharge_sync(
        db,
        device_id=device_id,
        fencing_token=payload.fencing_token,
        family_results=payload.family_results,
    )
    if not result.get("ok"):
        code = result.get("code", "DISCHARGE_REJECTED")
        status = 404 if code == "DEVICE_NOT_FOUND" else 409
        raise HTTPException(
            status_code=status,
            detail={"code": code, "message": "清偿被拒绝"},
        )
    db.commit()
    if result.get("rejected_families"):
        raise HTTPException(status_code=422, detail={
            "code": "UNVERIFIED_PROBE_RESULT", "message": "部分专项的探针组合未经验证",
            "violations": result["rejected_families"],
            "discharged_families": result.get("discharged_families") or [],
        })
    return ok(AgentReturnDischargeOut(
        device_id=device_id,
        discharged_families=result.get("discharged_families") or [],
        debt_lease_released=bool(result.get("debt_lease_released")),
        open_debt_count=int(result.get("open_debt_count") or 0),
        fencing_token=result.get("fencing_token"),
    ))
