# -*- coding: utf-8 -*-
"""MTBF 工具 API（P0）— runtask.xml 预览/校验。

契约见 docs/operations/mtbf-api.md §1；校验规则见 backend/services/mtbf_suite.py。
仅只读校验，无写操作。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import User, get_current_active_user
from backend.core.database import get_db
from backend.services.mtbf_suite import (
    analyze_runtask,
    parse_global_params,
    preview_payload,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/mtbf", tags=["mtbf"])

_MAX_RUNTASK_BYTES = 10 * 1024 * 1024  # runtask.xml 实为 ~77KB；上限防滥用
_GLOBAL_FILENAME = "UiAutomatorTestData.xml"


def _read_control_plane_path(path: str) -> bytes:
    """读取控制面可达路径（JSON 输入源）。越界/不存在返回 HTTP 错误。"""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "PATH_UNREADABLE",
                "message": f"cannot read path: {exc}",
            },
        ) from exc
    if len(data) > _MAX_RUNTASK_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "FILE_TOO_LARGE",
                "message": f"file exceeds {_MAX_RUNTASK_BYTES} bytes",
            },
        )
    return data


def _sibling_global(path: str) -> Optional[bytes]:
    """path 输入源时尝试同目录的 UiAutomatorTestData.xml（可选，用于引用推断）。"""
    sibling = Path(path).with_name(_GLOBAL_FILENAME)
    try:
        if sibling.is_file() and sibling.stat().st_size <= _MAX_RUNTASK_BYTES:
            return sibling.read_bytes()
    except OSError:
        pass
    return None


@router.post("/runtask/validate", response_model=ApiResponse[dict])
async def validate_runtask(
    request: Request,
    file: Optional[UploadFile] = File(None),
    global_file: Optional[UploadFile] = File(None, alias="global"),
    path: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_active_user),
):
    """上传 runtask.xml（可附 UiAutomatorTestData.xml）返回结构化预览与校验问题清单。

    输入源（P0 语义写死，见 P0 设计 §5.1）：
    - multipart 主路径：``file``（必填）+ ``global``（可选）；
    - JSON 备选：``{"path": "<控制面可达路径>"}``（仅控制面本地可达时）。
    """
    content: Optional[bytes] = None
    global_content: Optional[bytes] = None

    if file is not None:
        content = await file.read()
        if len(content) > _MAX_RUNTASK_BYTES:
            raise HTTPException(
                status_code=413,
                detail={"code": "FILE_TOO_LARGE", "message": f"file exceeds {_MAX_RUNTASK_BYTES} bytes"},
            )
        if global_file is not None:
            global_content = await global_file.read()
            if len(global_content) > _MAX_RUNTASK_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail={"code": "FILE_TOO_LARGE", "message": "global file too large"},
                )
    elif path:
        content = _read_control_plane_path(path)
        global_content = _sibling_global(path)
    else:
        ctype = (request.headers.get("content-type") or "").lower()
        if "application/json" in ctype:
            try:
                body = await request.json()
            except Exception as exc:  # json 解析失败按缺输入处理
                raise HTTPException(
                    status_code=400,
                    detail={"code": "INVALID_JSON", "message": f"body is not valid JSON: {exc}"},
                ) from exc
            raw_path = body.get("path") if isinstance(body, dict) else None
            if isinstance(raw_path, str) and raw_path:
                content = _read_control_plane_path(raw_path)
                global_content = _sibling_global(raw_path)

    if content is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "MISSING_INPUT",
                "message": "provide multipart 'file' (optionally 'global') or JSON {\"path\": ...}",
            },
        )

    analysis = analyze_runtask(content, global_params=parse_global_params(global_content) if global_content else None)
    return ok(
        {
            "valid": analysis.valid,
            "issues": [i.__dict__ for i in analysis.issues],
            "preview": preview_payload(analysis),
        }
    )
