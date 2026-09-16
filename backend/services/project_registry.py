"""项目登记簿写路径业务逻辑（#1520 垂直切片：projects.py 去 0-service）。

覆盖 create / update / rename / archive / unarchive / promote_seed 六条登记簿
生命周期，以及被多个路由复用的「取项目 + 来源/归档门禁」。路由退化为
「解析 → 调服务 → 序列化」；异常沿用 ``HTTPException``（与 #1519 / #1520
首个切片一致的服务层既有先例），``request`` 仅用于审计客户端 IP 提取（可空）。

边界：本模块**不**做响应装配（``ProjectSummaryOut`` 等留在路由），也不碰
inventory / map 映射链（那些仍属 projects.py 的其它业务线）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from fastapi import HTTPException, Request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.audit import record_audit
from backend.models.host import Device
from backend.models.project import SEED_PROJECT_KEYS, TestProject
from backend.models.project_model import ProjectModel
from backend.realtime.socketio_server import emit_project_changed

USER_SOURCE = "USER"
SEED_SOURCE = "SEED"

#: 可被 ``PUT /projects/{key}`` 修改的 facet —— 其余字段（platform/key 等）不可改。
UPDATABLE_FIELDS = (
    "display_name",
    "customer",
    "jira_project_key",
)


def get_project_or_404(db: Session, project_key: str) -> TestProject:
    project = (
        db.query(TestProject)
        .filter(TestProject.project_key == project_key)
        .first()
    )
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


def require_user_project(project: TestProject) -> None:
    if project.source != USER_SOURCE:
        raise HTTPException(
            status_code=422,
            detail="seed backfill labels cannot be mapped; create a user project",
        )


def require_active_project(project: TestProject) -> None:
    """归档守卫：ARCHIVED 项目只读（#644 P1-4 补齐）。

    rename / map preview / map apply / remove-rule 均须显式拒绝——归档后
    仍可改名会破坏「归档 = 冻结」的语义，仍可映射型号则归档形同虚设。
    """
    if project.status == "ARCHIVED":
        raise HTTPException(
            status_code=409,
            detail="archived project is read-only; unarchive to modify",
        )


def _ensure_key_available(db: Session, key: str) -> None:
    """SEED 保留名 + 大小写不敏感唯一性（创建/改名共用）。"""
    if key.upper() in SEED_PROJECT_KEYS:
        raise HTTPException(status_code=422, detail="reserved seed project_key")
    existing = (
        db.query(TestProject)
        .filter(func.lower(TestProject.project_key) == key.lower())
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="project_key already exists")


def create_project_entry(
    db: Session,
    *,
    project_key: str,
    display_name: str,
    customer: Optional[str],
    jira_project_key: Optional[str],
    actor_id: Optional[int],
    actor_username: Optional[str],
    request: Optional[Request] = None,
) -> TestProject:
    """admin 新建 USER 项目（SEED 保留名 422 / 重复 409，含并发 flush 兜底）。"""
    _ensure_key_available(db, project_key)
    project = TestProject(
        project_key=project_key,
        display_name=display_name.strip(),
        customer=customer,
        jira_project_key=jira_project_key,
        source=USER_SOURCE,
        status="ACTIVE",
    )
    db.add(project)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="project_key already exists") from None
    record_audit(
        db,
        action="create_project",
        resource_type="test_project",
        resource_id=project.id,
        details={"project_key": project_key},
        user_id=actor_id,
        username=actor_username,
        request=request,
    )
    db.commit()
    db.refresh(project)
    emit_project_changed(project.id, "created")
    return project


def update_project_facets(
    db: Session,
    *,
    project_key: str,
    provided: Mapping[str, Any],
    actor_id: Optional[int],
    actor_username: Optional[str],
    request: Optional[Request] = None,
) -> TestProject:
    """facet 修改，逐字段 ``record_audit``（ADR-0029 D2 / #406）。

    ``provided`` 只含请求显式给出的键；全部与现值相同（或无键可改）时不提交、
    不发变更事件，直接返回当前对象——与切分前的行为逐字一致。
    """
    project = get_project_or_404(db, project_key)
    require_user_project(project)
    if project.status == "ARCHIVED":
        raise HTTPException(status_code=409, detail="archived project cannot be updated")

    changed: list[tuple[str, object, object]] = []
    for field in UPDATABLE_FIELDS:
        if field not in provided:
            continue
        new_value = provided[field]
        old_value = getattr(project, field)
        if old_value == new_value:
            continue
        setattr(project, field, new_value)
        changed.append((field, old_value, new_value))

    if not changed:
        return project

    project.updated_at = datetime.now(timezone.utc)
    for field, old_value, new_value in changed:
        record_audit(
            db,
            action="update_project",
            resource_type="test_project",
            resource_id=project.id,
            details={
                "project_key": project.project_key,
                "field": field,
                "old": old_value,
                "new": new_value,
            },
            user_id=actor_id,
            username=actor_username,
            request=request,
        )
    db.commit()
    db.refresh(project)
    emit_project_changed(project.id, "updated")
    return project


def rename_project_entry(
    db: Session,
    *,
    project_key: str,
    new_key: str,
    actor_id: Optional[int],
    actor_username: Optional[str],
    request: Optional[Request] = None,
) -> TestProject:
    """ADR-0029 D2 复核：项目重命名（admin，记审计）。

    key 是用户指定标识（创建时手填），外键全用数字 project_id——改名
    不影响 device/plan/plan_run 归属。影响面：旧 URL 404（新 URL 生效）、
    历史审计显示旧 key（留痕）。SEED 保留名仍不可作新 key。
    """
    project = get_project_or_404(db, project_key)
    require_user_project(project)
    require_active_project(project)
    _ensure_key_available(db, new_key)

    old_key = project.project_key
    project.project_key = new_key
    project.updated_at = datetime.now(timezone.utc)
    record_audit(
        db,
        action="rename_project",
        resource_type="test_project",
        resource_id=project.id,
        details={
            "from_project_key": old_key,
            "to_project_key": new_key,
        },
        user_id=actor_id,
        username=actor_username,
        request=request,
    )
    db.commit()
    db.refresh(project)
    emit_project_changed(project.id, "renamed")
    return project


def archive_project_entry(
    db: Session,
    *,
    project_key: str,
    actor_id: Optional[int],
    actor_username: Optional[str],
    request: Optional[Request] = None,
) -> TestProject:
    """ADR-0029 D2 / #406 — 归档（SEED 回填标签也可归档 = 显式放弃）。"""
    project = get_project_or_404(db, project_key)
    if project.status == "ARCHIVED":
        raise HTTPException(status_code=409, detail="project already archived")

    project.status = "ARCHIVED"
    project.updated_at = datetime.now(timezone.utc)
    record_audit(
        db,
        action="archive_project",
        resource_type="test_project",
        resource_id=project.id,
        details={
            "project_key": project.project_key,
            "from_status": "ACTIVE",
            "to_status": "ARCHIVED",
        },
        user_id=actor_id,
        username=actor_username,
        request=request,
    )
    db.commit()
    db.refresh(project)
    emit_project_changed(project.id, "archived")
    return project


def unarchive_project_entry(
    db: Session,
    *,
    project_key: str,
    actor_id: Optional[int],
    actor_username: Optional[str],
    request: Optional[Request] = None,
) -> TestProject:
    """#644 P1-4 — 解档：ARCHIVED 项目恢复 ACTIVE（admin，记审计）。

    归档守卫补齐后的必要另一半——没有解档端点，「归档 = 冻结」就是单行道，
    误归档无法撤销。SEED 行从未被归档（v2.5 前不可归档；本版起允许），
    对 ACTIVE 行调用幂等地 409（与 archive 对称）。
    """
    project = get_project_or_404(db, project_key)
    if project.status != "ARCHIVED":
        raise HTTPException(status_code=409, detail="project is not archived")

    project.status = "ACTIVE"
    project.updated_at = datetime.now(timezone.utc)
    record_audit(
        db,
        action="unarchive_project",
        resource_type="test_project",
        resource_id=project.id,
        details={
            "project_key": project.project_key,
            "from_status": "ARCHIVED",
            "to_status": "ACTIVE",
        },
        user_id=actor_id,
        username=actor_username,
        request=request,
    )
    db.commit()
    db.refresh(project)
    emit_project_changed(project.id, "unarchived")
    return project


def promote_seed_project_entry(
    db: Session,
    *,
    project_key: str,
    actor_id: Optional[int],
    actor_username: Optional[str],
    request: Optional[Request] = None,
) -> TestProject:
    """ADR-0029 P0：SEED 回填标签就地转正为 USER 项目（admin）。

    source SEED → USER；match_models 预填其持有设备的型号；设备归属不动
    （行身份即归属身份）。LEGACY 是兜底标签、不可转正。幂等语义：仅
    source=SEED 且 ACTIVE 可转正，重复调用 → 404（已非 SEED）。
    """
    # 惰性导入：project_mapping 顶层依赖本模块，避免循环。
    from backend.services.project_mapping import blank_to_none

    seed = (
        db.query(TestProject)
        .filter(TestProject.project_key == project_key)
        .first()
    )
    if seed is None or seed.source != SEED_SOURCE:
        raise HTTPException(status_code=404, detail="seed project not found")
    if project_key == "LEGACY":
        raise HTTPException(
            status_code=422,
            detail="LEGACY is the fallback bucket, not promotable",
        )
    if seed.status == "ARCHIVED":
        raise HTTPException(
            status_code=409,
            detail="seed project archived, not promotable",
        )

    models = sorted(
        {
            blank_to_none(m)
            for (m,) in (
                db.query(Device.model)
                .join(ProjectModel, Device.model == ProjectModel.match_value)
                .filter(
                    ProjectModel.project_id == seed.id,
                    ProjectModel.is_active.is_(True),
                    Device.model.is_not(None),
                )
                .all()
            )
            if blank_to_none(m)
        }
    )
    seed.source = USER_SOURCE
    existing_members = {
        m
        for (m,) in db.query(ProjectModel.match_value)
        .filter(
            ProjectModel.project_id == seed.id,
            ProjectModel.is_active.is_(True),
        )
        .all()
    }
    for model in models:
        if model in existing_members:
            continue  # 幂等：成员行已存在（如带外预建）不重复插入
        db.add(
            ProjectModel(
                project_id=seed.id,
                match_value=model,
                created_by=actor_id,
            )
        )
    record_audit(
        db,
        action="promote_seed_project",
        resource_type="test_project",
        resource_id=seed.id,
        details={
            "project_key": project_key,
            "match_models": models,
        },
        user_id=actor_id,
        username=actor_username,
        request=request,
    )
    db.commit()
    db.refresh(seed)
    emit_project_changed(seed.id, "promoted")
    return seed
