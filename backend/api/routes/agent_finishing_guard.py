"""Agent finishing-guard handshake endpoints (ADR-0065 / B12 U4 / #4206).

Split from ``agent_api`` to keep that god-file under the line ceiling (#736).
Mounted under the same ``/api/v1/agent`` prefix.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.api.response import ok
from backend.api.routes.auth import verify_agent_secret as _verify_agent
from backend.core.database import get_db
from backend.services.finishing_guard import (
    ActivatePermitIn,
    CancelPreRegistrationIn,
    FinishingGuardError,
    GuardHeartbeatIn,
    PreRegisterIn,
    activate_permit_sync,
    cancel_pre_registration_sync,
    pre_register_atomic_commit_sync,
    record_guard_heartbeat_sync,
)

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


def _finishing_guard_http(exc: FinishingGuardError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"code": exc.code, **exc.extra},
    )


@router.post("/jobs/{job_id}/finishing-guard/pre-register")
def pre_register_finishing_guard(
    job_id: int,
    payload: PreRegisterIn,
    db: Session = Depends(get_db),
    _=Depends(_verify_agent),
):
    try:
        out = pre_register_atomic_commit_sync(db, job_id=job_id, payload=payload)
        db.commit()
        return ok(out)
    except FinishingGuardError as exc:
        db.rollback()
        raise _finishing_guard_http(exc) from exc


@router.post("/jobs/{job_id}/finishing-guard/activate-permit")
def activate_finishing_guard_permit(
    job_id: int,
    payload: ActivatePermitIn,
    host_id: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
    _=Depends(_verify_agent),
):
    try:
        out = activate_permit_sync(
            db, job_id=job_id, host_id=host_id, payload=payload,
        )
        db.commit()
        return ok(out)
    except FinishingGuardError as exc:
        db.rollback()
        raise _finishing_guard_http(exc) from exc


@router.post("/jobs/{job_id}/finishing-guard/cancel-pre-registration")
def cancel_finishing_guard_pre_registration(
    job_id: int,
    payload: CancelPreRegistrationIn,
    db: Session = Depends(get_db),
    _=Depends(_verify_agent),
):
    try:
        out = cancel_pre_registration_sync(db, job_id=job_id, payload=payload)
        db.commit()
        return ok(out)
    except FinishingGuardError as exc:
        db.rollback()
        raise _finishing_guard_http(exc) from exc


@router.post("/jobs/{job_id}/finishing-guard/heartbeat")
def finishing_guard_heartbeat(
    job_id: int,
    payload: GuardHeartbeatIn,
    db: Session = Depends(get_db),
    _=Depends(_verify_agent),
):
    try:
        out = record_guard_heartbeat_sync(db, job_id=job_id, payload=payload)
        db.commit()
        return ok(out)
    except FinishingGuardError as exc:
        db.rollback()
        raise _finishing_guard_http(exc) from exc
