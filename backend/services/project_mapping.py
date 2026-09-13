"""项目型号映射写路径（#1520 垂直切片第二刀：projects.py 去 0-service）。

覆盖 `map/preview`、`map/apply`、`rules/{model}` 删除三条写路径及型号归一助手。
路由退化为「解析 → 调服务 → 序列化」；异常沿用 ``HTTPException``（既有切片先例）。

边界（与 #1805 的「历史集合 vs 可发命令目标」切分呼应）：本模块只写**成员行**
（v2.5 D10 派生归属的唯一真源），不写设备列、不删快照、不碰派发面。
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.api.schemas.project import ProjectMapConflictOut, ProjectMapPreviewOut
from backend.core.audit import record_audit
from backend.models.host import Device
from backend.models.project import TestProject
from backend.models.project_model import ProjectModel
from backend.realtime.socketio_server import emit_project_changed
from backend.services.project_registry import (
    USER_SOURCE,
    get_project_or_404,
    require_active_project,
    require_user_project,
)


def blank_to_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def normalize_models(models: list[str]) -> list[str]:
    """去重 + strip + 统一大写（#644 P2 大小写不对称）。

    唯一索引按 ``lower(match_value)`` 防双归属，但 join 是全等比较——成员行
    与设备 model 大小写不一致会让「项目卡片显示已映射、inventory 显示未
    映射」两边同时正确。生产设备 model 事实全大写（getprop 上报），写入
    统一大写即与事实对齐。若未来设备侧出现小写型号，需 join 归一（另案）。
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in models:
        model = blank_to_none(raw)
        if model is None:
            continue
        model = model.upper()
        if model in seen:
            continue
        seen.add(model)
        out.append(model)
    return out


def map_preview(
    db: Session, project: TestProject, models: list[str], reassign_conflicts: bool,
) -> tuple[ProjectMapPreviewOut, list[Device], dict[str, str]]:
    names = normalize_models(models)
    if not names:
        raise HTTPException(status_code=422, detail="models must not be empty")
    # 设备事实按归一匹配（#644 大小写归一回归：设备 model 可能是混合大小写
    # 如 Infinix_X1102D，in_(大写) 全等会 miss——UI 勾选设备事实后映射路径
    # 被锁死）
    lowered = [n.lower() for n in names]
    devices = (
        db.query(Device)
        .filter(func.lower(Device.model).in_(lowered))
        .all()
    )
    # 归一值 → 设备事实原值（apply 写成员行用原值，join 才全等命中）
    model_facts: dict[str, str] = {}
    for d in devices:
        m = blank_to_none(d.model)
        if m is not None:
            model_facts.setdefault(m.upper(), m)
    present_lower = {(blank_to_none(d.model) or "").lower() for d in devices}
    unknown = [name for name in names if name.lower() not in present_lower]
    # 型号级归属（v2.5 D10 派生）：当前项目 = 型号的活跃成员行
    model_to_project = {
        match_value: (pid, key, source)
        for pid, match_value, key, source in (
            db.query(
                ProjectModel.project_id, ProjectModel.match_value,
                TestProject.project_key, TestProject.source,
            )
            .join(TestProject, TestProject.id == ProjectModel.project_id)
            .filter(ProjectModel.is_active.is_(True))
            .all()
        )
    }
    will: list[Device] = []
    already = 0
    conflicts: list[ProjectMapConflictOut] = []
    for device in devices:
        entry = model_to_project.get(blank_to_none(device.model))
        if entry is not None and entry[0] == project.id:
            already += 1
            continue
        is_user = entry is not None and entry[2] == USER_SOURCE
        if is_user and not reassign_conflicts:
            conflicts.append(
                ProjectMapConflictOut(
                    device_id=device.id,
                    serial=device.serial,
                    model=blank_to_none(device.model),
                    from_project_key=entry[1],
                )
            )
            continue
        will.append(device)
    preview = ProjectMapPreviewOut(
        target_project_key=project.project_key,
        models=names,
        will_assign=len(will),
        already_in_target=already,
        conflicts=conflicts,
        unknown_models=unknown,
    )
    return preview, will, model_facts


def preview_project_mapping(
    db: Session,
    *,
    project_key: str,
    models: list[str],
    reassign_conflicts: bool,
) -> ProjectMapPreviewOut:
    """admin 预览：型号 → 归属变化（含冲突/未知型号），不写任何状态。"""
    project = get_project_or_404(db, project_key)
    require_user_project(project)
    require_active_project(project)
    preview, _devices, _facts = map_preview(
        db, project, models, reassign_conflicts,
    )
    return preview


def apply_project_mapping(
    db: Session,
    *,
    project_key: str,
    models: list[str],
    reassign_conflicts: bool,
    actor_id: Optional[int],
    actor_username: Optional[str],
    request: Optional[Request] = None,
) -> ProjectMapPreviewOut:
    """admin 应用映射（写 project_model 成员行 + 审计 + 广播）。"""
    project = get_project_or_404(db, project_key)
    require_user_project(project)
    require_active_project(project)
    preview, _to_assign, model_facts = map_preview(
        db, project, models, reassign_conflicts,
    )
    if preview.conflicts:
        raise HTTPException(
            status_code=409,
            detail="models already mapped to another user project",
        )
    # ADR-0029 P1：规则写入 project_model（活跃唯一索引兜底——同型号
    # 双归属 INSERT 即 IntegrityError，preview 设备级冲突之外的双保险）。
    # v2.5 语义与 preview 对齐（M3 修正）：SEED 项目占用不算冲突（让位），
    # 仅 USER 项目占用需 reassign_conflicts。
    # #644 回归修复：existing 按归一匹配（lower(lower 唯一索引口径）——
    # 混合大小写旧行（如 Infinix_X1102D）必须命中，让位才生效；新行写
    # **设备事实原值**（model_facts），否则 join 全等 miss 使设备归零。
    for model in preview.models:
        existing = db.execute(
            select(ProjectModel)
            .where(
                func.lower(ProjectModel.match_value) == func.lower(model),
                ProjectModel.is_active.is_(True),
            )
        ).scalar_one_or_none()
        if existing is not None and existing.project_id != project.id:
            owner = db.get(TestProject, existing.project_id)
            if owner is not None and owner.source == USER_SOURCE and not reassign_conflicts:
                raise HTTPException(
                    status_code=409,
                    detail=f"model {model} already ruled to another project",
                )
            # SEED 占用或 reassign：旧成员行让位（uq 只约束活跃行）
            existing.is_active = False
        if existing is not None and existing.project_id == project.id:
            # #752：同项目大小写变体行必须收敛到设备事实原值——否则 join 全等
            # miss → 预览报 will_assign>0 但设备静默未归属（诊断从 500 变静默 200）。
            fact = model_facts.get(model, model)
            if existing.match_value != fact:
                existing.match_value = fact
        if existing is None or existing.project_id != project.id:
            try:
                db.add(ProjectModel(
                    project_id=project.id,
                    match_value=model_facts.get(model, model),
                    created_by=actor_id,
                ))
                db.flush()  # 立即触发唯一约束检查（IntegrityError 在此抛出）
            except IntegrityError:
                # 并发双写最后一道：existing 检查与 INSERT 之间被另一请求
                # 抢占同一归一型号——409 而非 500（用户可重试）
                db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail=f"model {model} concurrently claimed by another project",
                ) from None
    # v2.5 D10 M3：归属派生——apply 只写成员行，不写设备列（无副本可写）
    record_audit(
        db,
        action="apply_project_model",
        resource_type="test_project",
        resource_id=project.id,
        details={
            "project_key": project.project_key,
            "models": preview.models,
            "assigned_count": preview.will_assign,
        },
        user_id=actor_id,
        username=actor_username,
        request=request,
    )
    db.commit()
    # 归属变更必须广播——否则 B 端陈旧缓存可一路放行到派发（ADR-0029 D8）。
    emit_project_changed(project.id, "assigned")
    return preview


def remove_project_mapping_rule(
    db: Session,
    *,
    project_key: str,
    model: str,
    actor_id: Optional[int],
    actor_username: Optional[str],
    request: Optional[Request] = None,
) -> dict[str, Any]:
    """删除项目的一条活跃型号成员行（admin，记审计）。

    成员声明可撤回（map/apply 对已归属别的项目的型号 409，错误映射
    如生产 A57→MLD_LX2 残留曾无法通过平台修正）：型号脱离项目后，
    该型号设备在派生读路径下立即回归「未映射」（v2.5 D10 归属派生化——
    无副本可写，无需任何收敛机制）。
    """
    project = get_project_or_404(db, project_key)
    require_user_project(project)
    require_active_project(project)
    rule = db.execute(
        select(ProjectModel)
        .where(
            ProjectModel.project_id == project.id,
            ProjectModel.match_value == model,
            ProjectModel.is_active.is_(True),
        )
    ).scalar_one_or_none()
    if rule is None:
        raise HTTPException(
            status_code=404,
            detail=f"no active rule for model {model}",
        )
    db.delete(rule)
    record_audit(
        db,
        action="remove_project_model",
        resource_type="test_project",
        resource_id=project.id,
        details={
            "project_key": project.project_key,
            "model": model,
        },
        user_id=actor_id,
        username=actor_username,
        request=request,
    )
    db.commit()
    emit_project_changed(project.id, "rule_removed")
    return {"project_key": project.project_key, "model": model}
