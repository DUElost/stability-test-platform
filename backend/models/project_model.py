"""ProjectModel — 项目成员定义（ADR-0029 v2.5 D10 M1 收敛）。

成员定义：项目 = 机型集合的唯一事实源（model → project 全函数，
部分唯一索引 uq_project_model_active 保证同一型号不能双归属）。v2.5 D10
归属派生化后本表是归属读路径的唯一来源（device.model ⋈ 本表）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import relationship

from backend.core.database import Base

# SERIAL 预留已放弃（v2.5 D10：例外走 device_project_override），列已收敛删除
MATCH_TYPES = ("MODEL",)


class ProjectModel(Base):
    __tablename__ = "project_model"

    id          = Column(Integer, primary_key=True)
    project_id  = Column(Integer, ForeignKey("test_project.id", ondelete="CASCADE"),
                         nullable=False)
    match_value = Column(String(128), nullable=False)
    is_active   = Column(Boolean, nullable=False, default=True)
    created_at  = Column(DateTime(timezone=True),
                         default=lambda: datetime.now(timezone.utc))
    created_by  = Column(Integer, nullable=True)

    project = relationship("backend.models.project.TestProject",
                           foreign_keys=[project_id])

    __table_args__ = (
        Index(
            "uq_project_model_active",
            text("lower(match_value)"),
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )
