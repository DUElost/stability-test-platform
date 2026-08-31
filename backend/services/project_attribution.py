"""项目归属派生查询（ADR-0029 v2.5 D10）——model → project 的唯一事实源。

归属 = device.model ⋈ project_model（活跃成员行）。无副本列、无 pinned
例外——例外走未来的 device_project_override（当前无真实需求）。
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.project_model import ProjectModel

logger = logging.getLogger(__name__)


def resolve_project_id(db: Session, model: Optional[str]) -> Optional[int]:
    """活跃成员行精确匹配 model → project_id（派生前置查询）。

    无成员命中 → None（型号未映射）。
    """
    if not model:
        return None
    rule = db.execute(
        select(ProjectModel.project_id)
        .where(
                        ProjectModel.match_value == model,
            ProjectModel.is_active.is_(True),
        )
    ).scalar_one_or_none()
    return rule


def resolve_rules_for_model(db: Session, model: Optional[str]) -> list[ProjectModel]:
    """查询某型号的全部活跃成员行（map/preview、重算用）。"""
    if not model:
        return []
    rows = db.execute(
        select(ProjectModel)
        .where(
                        ProjectModel.match_value == model,
            ProjectModel.is_active.is_(True),
        )
        .order_by(ProjectModel.id)
    ).scalars().all()
    return list(rows)
