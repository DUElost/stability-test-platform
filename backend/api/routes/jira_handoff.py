"""B11-U1（#4199 / #4174）A 段只读提单表单生成 REST 端点。

挂载前缀：``/api/v1/jira/handoff``。
- 全部端点要求登录（``get_current_active_user``）；
- 不写 15.4 共享根、不触发 ``aee_extract`` / ``-m 0``；
- 所有 A 段生成产物均标 ``status="PENDING_EXPORT"``。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import get_current_active_user
from backend.api.schemas.jira_handoff import (
    HandoffBundleCreateIn,
    HandoffBundleOut,
    HandoffBundleSummaryOut,
    HandoffPreflightIn,
    HandoffPreflightOut,
)
from backend.core.database import get_db
from backend.models.user import User
from backend.services.jira_handoff_builder import (
    JiraHandoffError,
    create_handoff_bundle,
    get_handoff_bundle,
    list_handoff_bundles,
    resolve_handoff_download_file,
    run_handoff_preflight,
)

router = APIRouter(prefix="/api/v1/jira/handoff", tags=["jira-handoff"])


def _raise_http(exc: JiraHandoffError) -> None:
    detail: object = (
        {"code": exc.code, "message": exc.detail, **(exc.payload or {})}
        if exc.payload is not None
        else {"code": exc.code, "message": exc.detail}
    )
    raise HTTPException(status_code=exc.status_code, detail=detail) from exc


@router.post("/preflight", response_model=ApiResponse[HandoffPreflightOut])
def preflight_handoff(
    body: HandoffPreflightIn,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_active_user),
):
    """A 段只读双门禁预检：核验主机覆盖率、发生身份绑定率与 build 分布。"""
    try:
        result = run_handoff_preflight(
            db,
            plan_run_ids=body.plan_run_ids,
            platform=body.platform,
            snapshot_cutoff=body.snapshot_cutoff,
        )
    except JiraHandoffError as exc:
        _raise_http(exc)
    return ok(HandoffPreflightOut.model_validate(result))


@router.post("/bundles", response_model=ApiResponse[HandoffBundleOut])
def generate_handoff_bundle(
    body: HandoffBundleCreateIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """执行 A 段只读提单表单生成，产出冻结 ``manifest.json``、15 列预览表与 5 页复核工作簿。"""
    try:
        manifest = create_handoff_bundle(
            db,
            plan_run_ids=body.plan_run_ids,
            platform=body.platform,
            snapshot_cutoff=body.snapshot_cutoff,
            builds=body.builds,
            allow_incomplete_coverage=body.allow_incomplete_coverage,
            target_folder_hint=body.target_folder_hint,
            user_id=getattr(user, "id", None),
            username=getattr(user, "username", None),
            request=request,
        )
    except JiraHandoffError as exc:
        _raise_http(exc)
    return ok(HandoffBundleOut.model_validate(manifest))


@router.get("/bundles", response_model=ApiResponse[list[HandoffBundleSummaryOut]])
def list_bundles(
    limit: int = Query(default=20, ge=1, le=100),
    _user: User = Depends(get_current_active_user),
):
    """列出近期 A 段提单表单交付包摘要。"""
    try:
        items = list_handoff_bundles(limit=limit)
    except JiraHandoffError as exc:
        _raise_http(exc)
    return ok([HandoffBundleSummaryOut.model_validate(item) for item in items])


@router.get("/bundles/{handoff_id}", response_model=ApiResponse[HandoffBundleOut])
def get_bundle_detail(
    handoff_id: str,
    _user: User = Depends(get_current_active_user),
):
    """读取单个 A 段交付包的冻结 manifest 详情。"""
    try:
        manifest = get_handoff_bundle(handoff_id)
    except JiraHandoffError as exc:
        _raise_http(exc)
    return ok(HandoffBundleOut.model_validate(manifest))


@router.get("/bundles/{handoff_id}/files/{artifact_kind}")
def download_bundle_file(
    handoff_id: str,
    artifact_kind: str,
    build: Optional[str] = Query(default=None),
    _user: User = Depends(get_current_active_user),
):
    """按白名单安全下载 A 段产物（``manifest`` / ``preview_xls`` / ``review_workbook``）。"""
    try:
        path, filename, media_type = resolve_handoff_download_file(
            handoff_id,
            artifact_kind,
            build=build,
        )
    except JiraHandoffError as exc:
        _raise_http(exc)
    return FileResponse(path=str(path), filename=filename, media_type=media_type)
