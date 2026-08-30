"""项目归属解析（ADR-0029 P1）——规则表 → device.project_id 的唯一应用路径。

分层：规则（project_model，admin 显式声明）→ 解析（本模块）→
应用（心跳 / 规则变更 / reconcile sweep）。解析是纯函数（只读规则表），
应用点负责「何时写 device.project_id」：
- 心跳：新建 Device 后 / model 变更后 / project_id IS NULL（稳态三个条件
  全不满足，零额外查询）
- 规则变更（map/apply、规则删除）：受影响型号的设备重算
- 夜间 sweep：兜底漂移检测（P1 阶段实现为记录，不自动改）

pinned 语义：device.project_pinned = true 的设备**永不被规则覆盖**——人工
钉住是唯一允许同型号拆两个项目的方式，显式可列出。

改判告警：已归属设备被规则改判（model 变更 / 历史错误归属）必须留痕——
一条错规则会静默搬走整批设备（生产事故：A57→MLD_LX2 残留规则）。
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.host import Device
from backend.models.project_model import ProjectModel

logger = logging.getLogger(__name__)


def resolve_project_id(db: Session, model: Optional[str]) -> Optional[int]:
    """活跃规则精确匹配 model → project_id。

    无规则命中 → None（显式「待归属」，不猜）。match_type 预留 SERIAL，
    当前仅 MODEL 有写入路径。
    """
    if not model:
        return None
    rule = db.execute(
        select(ProjectModel.project_id)
        .where(
            ProjectModel.match_type == "MODEL",
            ProjectModel.match_value == model,
            ProjectModel.is_active.is_(True),
        )
    ).scalar_one_or_none()
    return rule


def apply_attribution(db: Session, device: Device) -> bool:
    """按规则应用归属到单台设备；返回是否发生变更。

    人工优先：project_pinned 的设备不动。规则未命中保持现状（不抹除已有
    归属——归属错了改规则/改钉住，不是让心跳把设备清空）。
    """
    if device.project_pinned:
        return False
    if device.project_id is not None and device.model is None:
        # 无型号无从判定；保持现状（不抹除）
        return False
    resolved = resolve_project_id(db, device.model)
    if resolved is None:
        return False
    if device.project_id == resolved:
        return False
    # 改判告警：已归属设备被规则改判（model 变更 / 历史错误归属）——
    # 一条错规则会静默搬走整批设备，必须留痕（生产事故复盘结论）。
    if device.project_id is not None:
        logger.warning(
            "attribution_reassigned serial=%s model=%s from_project=%s to_project=%s",
            device.serial, device.model, device.project_id, resolved,
        )
    device.project_id = resolved
    return True


def resolve_rules_for_model(db: Session, model: Optional[str]) -> list[ProjectModel]:
    """查询某型号的全部活跃规则（map/preview、规则删除重算用）。"""
    if not model:
        return []
    rows = db.execute(
        select(ProjectModel)
        .where(
            ProjectModel.match_type == "MODEL",
            ProjectModel.match_value == model,
            ProjectModel.is_active.is_(True),
        )
        .order_by(ProjectModel.id)
    ).scalars().all()
    return list(rows)
