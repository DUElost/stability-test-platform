# -*- coding: utf-8 -*-
"""ADR-0030 P1a — MTBF 套件/用例管理面（外部 agent 的 REST 入口）。

契约见 docs/operations/mtbf-api.md §2、P1 设计 §2。读 = 登录用户、写 = admin，
全部写操作 ``record_audit``。

**导出一致性不靠端点纪律**（P1 设计 §2 总则）：本模块任何写端点都不清快照列，
库漂移由 ``content_fingerprint`` 在门禁/详情处**计算**得出。将来新增写端点
无需记得做任何事——这正是选结构性检测而非置空枚举的理由。
"""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from sqlalchemy.orm import Session, joinedload

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import User, get_current_active_user, require_admin
from backend.api.schemas.test_suite import (
    ExportResultOut,
    TestCaseIn,
    TestCaseOut,
    TestSuiteCreateIn,
    TestSuiteDetailOut,
    TestSuiteOut,
    TestSuiteUpdateIn,
    ValidateOut,
)
from backend.core.audit import record_audit
from backend.core.database import get_db
from backend.core.storage_root import resolve_shared_storage_root
from backend.models.project import TestProject
from backend.models.test_suite import TestCase, TestSuite
from backend.services.mtbf_suite import (
    _validate_suite,
    content_fingerprint,
    exec_desc_to_dict,
    parse_global_params,
    parse_runtask,
    render_global,
    render_runtask,
    suite_from_rows,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["test-suites"])

_MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_RUNTASK_NAME = "runtask.xml"
_GLOBAL_NAME = "UiAutomatorTestData.xml"


# ---------------------------------------------------------------------------
# 内部助手
# ---------------------------------------------------------------------------

def _case_rows(db: Session, suite_id: int) -> List[dict]:
    """按 ordinal 取用例行。

    显式查询而非走 ``suite.cases`` 关系：会话若配 ``expire_on_commit=False``
    （测试 conftest 即如此），提交后已加载的集合不会失效，identity map 会把
    过期集合喂给指纹计算——指纹一旦算在陈旧集合上，「库改了没导出」就漏检。
    """
    rows = (
        db.query(TestCase)
        .filter(TestCase.suite_id == suite_id)
        .order_by(TestCase.ordinal, TestCase.id)
        .all()
    )
    return [
        {
            "name": c.name,
            "ordinal": c.ordinal,
            "times": c.times,
            "enabled": c.enabled,
            "exec_descs": c.exec_descs or [],
        }
        for c in rows
    ]


def _current_fingerprint(db: Session, suite: TestSuite) -> str:
    return content_fingerprint(
        root_config=suite.root_config,
        global_params=suite.global_params,
        cases=_case_rows(db, suite.id),
    )


def _resolve_export_dir(suite: TestSuite) -> str:
    """导出目录：显式 export_dir > 项目 key > ``legacy``（兼容 P0 部署现状）。"""
    if suite.export_dir:
        return suite.export_dir
    if suite.project is not None:
        return suite.project.project_key
    return "legacy"


def _suite_out(db: Session, suite: TestSuite, detail: bool = False):
    cases = _case_rows(db, suite.id)
    current = _current_fingerprint(db, suite)
    payload = {
        "id": suite.id,
        "name": suite.name,
        "display_name": suite.display_name,
        "project_key": suite.project.project_key if suite.project else None,
        "export_dir": _resolve_export_dir(suite),
        "apk_binding": suite.apk_binding,
        "case_count": len(cases),
        "enabled_case_count": sum(1 for c in cases if c["enabled"]),
        "exported_sha256": suite.exported_sha256,
        "is_active": suite.is_active,
        # 未导出过也算 stale：门禁第 2 步会以 not_exported 拦下
        "export_stale": suite.exported_content_sha256 != current,
        "created_at": suite.created_at,
        "updated_at": suite.updated_at,
    }
    if not detail:
        return TestSuiteOut(**payload)
    payload.update(
        {
            "root_config": suite.root_config or {},
            "global_params": suite.global_params,
            "source_sha256": suite.source_sha256,
            "exported_content_sha256": suite.exported_content_sha256,
            "content_sha256": current,
        }
    )
    return TestSuiteDetailOut(**payload)


def _get_suite(db: Session, suite_id: int) -> TestSuite:
    suite = (
        db.query(TestSuite)
        .options(joinedload(TestSuite.project))
        .filter(TestSuite.id == suite_id)
        .first()
    )
    if suite is None:
        raise HTTPException(status_code=404, detail="test suite not found")
    return suite


def _resolve_project_id(db: Session, project_key: Optional[str]) -> Optional[int]:
    if not project_key:
        return None
    project = (
        db.query(TestProject).filter(TestProject.project_key == project_key).first()
    )
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project.id


async def _read_upload(upload: UploadFile, label: str) -> bytes:
    data = await upload.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"code": "FILE_TOO_LARGE", "message": f"{label} exceeds {_MAX_UPLOAD_BYTES} bytes"},
        )
    return data


# ---------------------------------------------------------------------------
# 套件 CRUD
# ---------------------------------------------------------------------------

@router.get("/api/v1/test-suites", response_model=ApiResponse[List[TestSuiteOut]])
def list_suites(
    project_key: Optional[str] = None,
    is_active: Optional[bool] = None,
    q: Optional[str] = None,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_active_user),
):
    """套件列表。未知 project_key → 404（与 ADR-0029 列表口径一致，不吞成空表）。"""
    query = db.query(TestSuite).options(joinedload(TestSuite.project))
    if project_key:
        query = query.filter(TestSuite.project_id == _resolve_project_id(db, project_key))
    if is_active is not None:
        query = query.filter(TestSuite.is_active.is_(is_active))
    if q:
        like = f"%{q}%"
        query = query.filter(TestSuite.name.ilike(like))
    return ok([_suite_out(db, s) for s in query.order_by(TestSuite.name).all()])


@router.post("/api/v1/test-suites", response_model=ApiResponse[TestSuiteDetailOut])
def create_suite(
    payload: TestSuiteCreateIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    if db.query(TestSuite).filter(TestSuite.name == payload.name).first():
        raise HTTPException(status_code=409, detail="suite name already exists")
    suite = TestSuite(
        name=payload.name,
        display_name=payload.display_name,
        project_id=_resolve_project_id(db, payload.project_key),
        export_dir=payload.export_dir,
        apk_binding=payload.apk_binding,
        root_config=payload.root_config or {},
        global_params=payload.global_params,
    )
    db.add(suite)
    db.flush()
    record_audit(
        db, action="create", resource_type="test_suite", resource_id=suite.id,
        details={"name": suite.name, "project_key": payload.project_key},
        user_id=current_user.id, username=current_user.username, request=request,
    )
    db.commit()
    return ok(_suite_out(db, _get_suite(db, suite.id), detail=True))


@router.get("/api/v1/test-suites/{suite_id}", response_model=ApiResponse[TestSuiteDetailOut])
def get_suite(
    suite_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_active_user),
):
    return ok(_suite_out(db, _get_suite(db, suite_id), detail=True))


@router.put("/api/v1/test-suites/{suite_id}", response_model=ApiResponse[TestSuiteDetailOut])
def update_suite(
    suite_id: int,
    payload: TestSuiteUpdateIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """更新元数据。**不清任何快照列**——库漂移由指纹计算检测（§2 总则）。"""
    suite = _get_suite(db, suite_id)
    fields = payload.model_dump(exclude_unset=True)
    if "project_key" in fields:
        suite.project_id = _resolve_project_id(db, fields.pop("project_key"))
    for key, value in fields.items():
        setattr(suite, key, value)
    record_audit(
        db, action="update", resource_type="test_suite", resource_id=suite.id,
        details={"name": suite.name, "changed": sorted(payload.model_dump(exclude_unset=True))},
        user_id=current_user.id, username=current_user.username, request=request,
    )
    db.commit()
    return ok(_suite_out(db, _get_suite(db, suite_id), detail=True))


@router.delete("/api/v1/test-suites/{suite_id}", response_model=ApiResponse[dict])
def delete_suite(
    suite_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """软删（is_active=false）。ACTIVE PlanRun 引用守卫在 P1b 随绑定字段落地。"""
    suite = _get_suite(db, suite_id)
    suite.is_active = False
    record_audit(
        db, action="deactivate", resource_type="test_suite", resource_id=suite.id,
        details={"name": suite.name},
        user_id=current_user.id, username=current_user.username, request=request,
    )
    db.commit()
    return ok({"id": suite_id, "is_active": False})


# ---------------------------------------------------------------------------
# 用例 CRUD
# ---------------------------------------------------------------------------

@router.get("/api/v1/test-suites/{suite_id}/cases", response_model=ApiResponse[List[TestCaseOut]])
def list_cases(
    suite_id: int,
    enabled: Optional[bool] = None,
    q: Optional[str] = None,
    skip: int = 0,
    limit: int = 500,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_active_user),
):
    _get_suite(db, suite_id)
    query = db.query(TestCase).filter(TestCase.suite_id == suite_id)
    if enabled is not None:
        query = query.filter(TestCase.enabled.is_(enabled))
    if q:
        query = query.filter(TestCase.name.ilike(f"%{q}%"))
    rows = query.order_by(TestCase.ordinal, TestCase.id).offset(skip).limit(limit).all()
    return ok([TestCaseOut.model_validate(c) for c in rows])


@router.post("/api/v1/test-suites/{suite_id}/cases", response_model=ApiResponse[TestCaseOut])
def create_case(
    suite_id: int,
    payload: TestCaseIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    suite = _get_suite(db, suite_id)
    if db.query(TestCase).filter(
        TestCase.suite_id == suite_id, TestCase.name == payload.name
    ).first():
        raise HTTPException(status_code=409, detail="case name already exists in suite")
    case = TestCase(suite_id=suite.id, **payload.model_dump())
    db.add(case)
    db.flush()
    record_audit(
        db, action="create", resource_type="test_case", resource_id=case.id,
        details={"suite": suite.name, "name": case.name},
        user_id=current_user.id, username=current_user.username, request=request,
    )
    db.commit()
    db.refresh(case)
    return ok(TestCaseOut.model_validate(case))


@router.put("/api/v1/test-cases/{case_id}", response_model=ApiResponse[TestCaseOut])
def update_case(
    case_id: int,
    payload: TestCaseIn,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """整覆盖（§7 #4）。任何字段变更都改渲染产物，但**无需清快照列**。"""
    case = db.query(TestCase).filter(TestCase.id == case_id).first()
    if case is None:
        raise HTTPException(status_code=404, detail="test case not found")
    clash = db.query(TestCase).filter(
        TestCase.suite_id == case.suite_id,
        TestCase.name == payload.name,
        TestCase.id != case_id,
    ).first()
    if clash is not None:
        raise HTTPException(status_code=409, detail="case name already exists in suite")
    for key, value in payload.model_dump().items():
        setattr(case, key, value)
    record_audit(
        db, action="update", resource_type="test_case", resource_id=case.id,
        details={"suite_id": case.suite_id, "name": case.name},
        user_id=current_user.id, username=current_user.username, request=request,
    )
    db.commit()
    db.refresh(case)
    return ok(TestCaseOut.model_validate(case))


@router.delete("/api/v1/test-cases/{case_id}", response_model=ApiResponse[dict])
def delete_case(
    case_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    case = db.query(TestCase).filter(TestCase.id == case_id).first()
    if case is None:
        raise HTTPException(status_code=404, detail="test case not found")
    record_audit(
        db, action="delete", resource_type="test_case", resource_id=case.id,
        details={"suite_id": case.suite_id, "name": case.name},
        user_id=current_user.id, username=current_user.username, request=request,
    )
    db.delete(case)
    db.commit()
    return ok({"id": case_id, "deleted": True})


# ---------------------------------------------------------------------------
# 导入 / 导出 / 校验
# ---------------------------------------------------------------------------

@router.post("/api/v1/test-suites/{suite_id}/import", response_model=ApiResponse[TestSuiteDetailOut])
async def import_suite(
    suite_id: int,
    request: Request,
    file: UploadFile = File(...),
    global_file: Optional[UploadFile] = File(None, alias="global"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """runtask.xml（可附 UiAutomatorTestData.xml）导入既有套件——用例按 name upsert。

    ordinal 按文件顺序重排：文件是权威顺序，导入即以它为准。文件中不存在的
    用例**删除**（整体替换语义），避免库里残留上一版用例悄悄进导出物。
    """
    suite = _get_suite(db, suite_id)
    content = await _read_upload(file, "runtask.xml")
    try:
        parsed = parse_runtask(content)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "XML_PARSE_ERROR", "message": str(exc)}
        ) from exc

    global_params = suite.global_params
    if global_file is not None:
        global_bytes = await _read_upload(global_file, _GLOBAL_NAME)
        global_params = {
            "sim": parse_global_params(global_bytes),
            "test_set_attrs": _parse_test_set_attrs(global_bytes),
            "test_package_ref": (global_params or {}).get("test_package_ref"),
        }

    existing = {c.name: c for c in db.query(TestCase).filter(TestCase.suite_id == suite.id).all()}
    seen: set = set()
    for ordinal, tp in enumerate(parsed.testpoints, start=1):
        descs = [exec_desc_to_dict(d) for d in tp.exec_descs]
        row = existing.get(tp.name)
        if row is None:
            db.add(TestCase(suite_id=suite.id, name=tp.name, ordinal=ordinal,
                            times=tp.times, exec_descs=descs))
        else:
            row.ordinal, row.times, row.exec_descs = ordinal, tp.times, descs
        seen.add(tp.name)
    removed = [c for name, c in existing.items() if name not in seen]
    for row in removed:
        db.delete(row)

    suite.root_config = parsed.root_config
    suite.global_params = global_params
    suite.source_sha256 = hashlib.sha256(content).hexdigest()
    record_audit(
        db, action="import", resource_type="test_suite", resource_id=suite.id,
        details={
            "name": suite.name,
            "testpoints": len(parsed.testpoints),
            "removed": len(removed),
            "source_sha256": suite.source_sha256,
        },
        user_id=current_user.id, username=current_user.username, request=request,
    )
    db.commit()
    return ok(_suite_out(db, _get_suite(db, suite_id), detail=True))


def _parse_test_set_attrs(content: bytes) -> dict:
    """取 UiAutomatorTestData.xml 根 <TestSet> 属性（导出时带回，勿丢 TakeScreenshot）。"""
    import xml.etree.ElementTree as ET

    try:
        return dict(ET.fromstring(content).attrib)
    except ET.ParseError:
        return {}


@router.get("/api/v1/test-suites/{suite_id}/export")
def export_runtask(
    suite_id: int,
    times: int = 0,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_active_user),
):
    """返回渲染的 runtask.xml 字节。库漂移时 ``X-Export-Stale: 1`` 提示需重导。"""
    suite = _get_suite(db, suite_id)
    body = render_runtask(
        suite_from_rows(name=suite.name, root_config=suite.root_config,
                        cases=_case_rows(db, suite.id)),
        times=times,
    )
    stale = suite.exported_content_sha256 != _current_fingerprint(db, suite)
    return Response(
        content=body,
        media_type="application/xml",
        headers={
            "Content-Disposition": f'attachment; filename="{_RUNTASK_NAME}"',
            "X-Export-Stale": "1" if stale else "0",
        },
    )


@router.get("/api/v1/test-suites/{suite_id}/global")
def export_global(
    suite_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_active_user),
):
    suite = _get_suite(db, suite_id)
    return Response(
        content=render_global(suite.global_params),
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{_GLOBAL_NAME}"'},
    )


@router.post("/api/v1/test-suites/{suite_id}/validate", response_model=ApiResponse[ValidateOut])
def validate_suite(
    suite_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_active_user),
):
    """校验**库内数据**（与 P0 的文件输入 validate 分工，见 P1 设计 §2 抬头）。"""
    suite = _get_suite(db, suite_id)
    built = suite_from_rows(name=suite.name, root_config=suite.root_config,
                            cases=_case_rows(db, suite.id))
    issues = _validate_suite(built, (suite.global_params or {}).get("sim"))
    return ok(
        ValidateOut(
            valid=not any(i.severity == "error" for i in issues),
            issues=[i.__dict__ for i in issues],
        )
    )


@router.post("/api/v1/test-suites/{suite_id}/export-to-tool-dir",
             response_model=ApiResponse[ExportResultOut])
def export_to_tool_dir(
    suite_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """渲染两文件 atomic write 到中心存储消费路径，并记下两个漂移比对基线。"""
    suite = _get_suite(db, suite_id)
    root = resolve_shared_storage_root()
    if not root:
        raise HTTPException(
            status_code=503,
            detail={"code": "STORAGE_ROOT_UNSET", "message": "STP_AEE_NFS_ROOT is not configured"},
        )
    target = Path(root) / "mtbf" / _resolve_export_dir(suite)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "EXPORT_DIR_UNWRITABLE", "message": str(exc)},
        ) from exc

    built = suite_from_rows(name=suite.name, root_config=suite.root_config,
                            cases=_case_rows(db, suite.id))
    runtask_bytes = render_runtask(built)
    global_bytes = render_global(suite.global_params)

    runtask_path = target / _RUNTASK_NAME
    global_path = target / _GLOBAL_NAME
    _atomic_write(runtask_path, runtask_bytes)
    _atomic_write(global_path, global_bytes)

    # 两个基线同一事务内写：先写盘后落库失败 → 门禁看列为空报 not_exported；
    # 先落库后写盘失败 → 磁盘无文件同样 not_exported。两向都 fail-closed。
    suite.exported_sha256 = hashlib.sha256(runtask_bytes).hexdigest()
    suite.exported_content_sha256 = _current_fingerprint(db, suite)
    record_audit(
        db, action="export", resource_type="test_suite", resource_id=suite.id,
        details={
            "name": suite.name,
            "export_dir": _resolve_export_dir(suite),
            "exported_sha256": suite.exported_sha256,
            "testpoints": len(built.testpoints),
        },
        user_id=current_user.id, username=current_user.username, request=request,
    )
    db.commit()
    return ok(
        ExportResultOut(
            export_dir=_resolve_export_dir(suite),
            runtask_path=str(runtask_path),
            global_path=str(global_path),
            exported_sha256=suite.exported_sha256,
            exported_content_sha256=suite.exported_content_sha256,
        )
    )


def _atomic_write(path: Path, data: bytes) -> None:
    """临时文件 + os.replace：消费方永远看不到半截文件。"""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
