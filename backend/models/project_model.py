"""ProjectModel — 项目成员定义（ADR-0029 v2.5 D10 M1 收敛）。

成员定义：项目 = 机型集合的唯一事实源（model → project 全函数，
部分唯一索引 uq_project_model_active 保证同一型号不能双归属）。由
project_attribution.resolve_project_id 消费。v2.5 D10 归属派生化后本表与
device.project_id 合二为一（副本删除，M3）；届时本表即归属的唯一来源。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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

# SERIAL 预留已放弃（v2.5 D10：例外走 device_project_override），当前仅 MODEL
MATCH_TYPES = ("MODEL",)


class ProjectModel(Base):
    __tablename__ = "project_model"

    id          = Column(Integer, primary_key=True)
    project_id  = Column(Integer, ForeignKey("test_project.id", ondelete="CASCADE"),
                         nullable=False)
    match_type  = Column(String(16), nullable=False, default="MODEL")
    match_value = Column(String(128), nullable=False)
    is_active   = Column(Boolean, nullable=False, default=True)
    created_at  = Column(DateTime(timezone=True),
                         default=lambda: datetime.now(timezone.utc))
    created_by  = Column(Integer, nullable=True)

    project = relationship("backend.models.project.TestProject",
                           foreign_keys=[project_id])

    __table_args__ = (
        CheckConstraint(
            "match_type IN ('MODEL', 'SERIAL')",
            name="ck_project_model_type",
        ),
        Index(
            "uq_project_model_active",
            "match_type",
            text("lower(match_value)"),
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )
