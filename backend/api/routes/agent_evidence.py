"""Agent 侧 S8 代表证据端点（B8 G6 / ADR-0062 D8/D9）。

路由只做鉴权、请求/响应适配与提交；校验、哈希复核、状态机与交付重算都在
backend.services.evidence_publication。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import verify_agent_secret as _verify_agent
from backend.core.database import get_db
from backend.models.issue_fact import EvidencePublication
from backend.services.evidence_publication import (
    EvidencePublicationError,
    EvidenceReceiptIn,
    list_agent_tasks,
    record_agent_receipt,
    refresh_delivery_and_archive,
)

router = APIRouter(prefix="/api/v1/agent", tags=["agent-evidence"])


@router.get("/evidence-publications", response_model=ApiResponse[dict])
def list_evidence_publications(
    host_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    _=Depends(_verify_agent),
):
    """Agent 拉取本机待上送的代表证据（只含 DEDUP_FIRST 的本地代表）。"""
    return ok({"publications": list_agent_tasks(db, host_id=host_id, limit=limit)})


@router.post(
    "/evidence-publications/{publication_id}/receipt", response_model=ApiResponse[dict],
)
def record_evidence_receipt(
    publication_id: int,
    payload: EvidenceReceiptIn,
    db: Session = Depends(get_db),
    _=Depends(_verify_agent),
):
    """接收端校验并落回执；VERIFIED 由控制面重新读取目的目录复核哈希。"""
    try:
        result = record_agent_receipt(
            db,
            publication_id=publication_id,
            host_id=payload.host_id,
            dle_id=payload.dle_id,
            destination_rel_path=payload.destination_rel_path,
            state=payload.state,
            checksum=payload.checksum,
            size_bytes=payload.size_bytes,
            attempts=payload.attempts,
            reason=payload.reason,
        )
        row = db.get(EvidencePublication, publication_id)
    except EvidencePublicationError as exc:
        db.rollback()
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    plan_run_id, platform = row.plan_run_id, row.platform
    db.commit()
    if result.get("state") in {"VERIFIED", "FAILED"}:
        # 只有进入可判定的终态才重算交付版本；回执中的 RETRYING 不改变
        # D9 的状态（仍是重试中），避免每轮回执都重算一遍交付物。
        refresh_delivery_and_archive(plan_run_id, platform)
    if result.get("error"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": result["error"],
                "message": result.get("message", "receipt not verified"),
            },
        )
    return ok(result)
