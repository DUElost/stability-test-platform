"""Admin finishing-guard admission close / drain (ADR-0065 D5.7 / B12 U4).

Kept out of ``plan_runs`` god-file to satisfy #736 line ceilings.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.api.response import ok
from backend.api.routes.auth import get_current_active_user, User
from backend.core.database import get_db
from backend.services.finishing_guard import (
    close_admission_sync,
    count_active_atomic_guards_sync,
    is_admission_open_sync,
)

router = APIRouter(prefix="/api/v1", tags=["finishing-guard"])


@router.post("/finishing-guard/admission/close")
def close_finishing_guard_admission_endpoint(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """ADR-0065 D5.7：回滚前关门（admission FOR UPDATE → admission_open=false）。"""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="admin required")
    out = close_admission_sync(db)
    db.commit()
    return ok(out)


@router.get("/finishing-guard/admission/drain-count")
def finishing_guard_admission_drain_count_endpoint(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """活跃 pre_registered/granted atomic_commit Job 计数（回滚排空核验）。"""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="admin required")
    return ok({
        "admission_open": is_admission_open_sync(db),
        "active_guards": count_active_atomic_guards_sync(db),
    })
