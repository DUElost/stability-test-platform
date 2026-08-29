"""ProjectDeviceRule — 机型/序列号 → 项目归属规则（ADR-0029 P1）。

规则层：admin 显式声明的 model → project 映射，活跃规则内是函数（部分
唯一索引 uq_rule_active 保证——同一型号不能同时归属两个项目）。由
project_attribution.resolve_project_id 消费；心跳路径只应用规则、不写规则。
test_project.match_models 列已于 P1 收尾 drop，读侧由 _rule_values_for_project
派生（API 契约不变）。
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

# 预留 SERIAL（按序列号归属），当前仅 MODEL 有写入路径
MATCH_TYPES = ("MODEL", "SERIAL")


class ProjectDeviceRule(Base):
    __tablename__ = "project_device_rule"

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
            name="ck_project_device_rule_type",
        ),
        Index(
            "uq_rule_active",
            "match_type",
            text("lower(match_value)"),
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )
