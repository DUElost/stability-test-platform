"""Plan ORM — ADR-0020.

Plan is the top-level orchestration unit.  Multi-stage execution is modeled
as an explicit Plan chain via next_plan_id.

ADR-0020 §2 唯一事实源：lifecycle 完全由 ``PlanStep`` 行 + ``patrol_interval_seconds``
+ ``timeout_seconds`` 重新组装；Plan 表上不再保留 ``lifecycle`` JSONB 列。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from backend.core.database import Base


class Plan(Base):
    __tablename__ = "plan"

    id                = Column(Integer, primary_key=True)
    name              = Column(String(256), nullable=False)
    description       = Column(Text)
    failure_threshold = Column(Float, nullable=False, default=0.05)
    patrol_interval_seconds = Column(Integer, nullable=True)
    timeout_seconds   = Column(Integer, nullable=True)
    # INIT→PATROL barrier 预算。NULL = 沿用 STP_BARRIER_TIMEOUT_SECONDS / 600s。
    # 这不是独立旋钮：只有先到者在等，所以它要覆盖同 host 的 init **落差**，
    # 而 init 受 permit cap 串行化 ⇒ 约 (ceil(N/C)−1)×T。含长耗时前置步骤
    # （自动刷机等）的 Plan 必须显式抬高，否则先做完的设备会被慢同伴连坐。
    barrier_timeout_seconds = Column(Integer, nullable=True)
    # #174: progress-aware barrier 绝对硬顶（从首次等待起算，NULL = 不设上限）。
    # 滑动窗 barrier_timeout_seconds 管「全体停滞」，本字段管「总等待时长」。
    barrier_max_wait_seconds = Column(Integer, nullable=True)
    auto_archive_interval_seconds = Column(Integer, nullable=True)
    next_plan_id      = Column(Integer, ForeignKey("plan.id"), nullable=True)
    watcher_policy    = Column(JSONB, nullable=True)
    # ADR-0029 归属（P1 M-a）：NULL = 迁移期瞬态，M-b 回填后归零（Legacy 承载存量 Plan）。
    project_id        = Column(Integer, ForeignKey("test_project.id"), nullable=True)
    # D6：专项字典表（MTBF / 开关机 / MONKEY / …），Plan 列表按 项目×专项 二维分组。
    specialty_id      = Column(Integer, ForeignKey("specialty.id"), nullable=True)
    created_by        = Column(String(128))
    created_at        = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at        = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    next_plan = relationship("Plan", remote_side=[id], foreign_keys=[next_plan_id])
    steps     = relationship("PlanStep", back_populates="plan", lazy="dynamic",
                             cascade="all, delete-orphan")
    runs      = relationship("PlanRun", back_populates="plan", lazy="dynamic",
                             primaryjoin="Plan.id == foreign(PlanRun.plan_id)")
    project   = relationship("TestProject", foreign_keys=[project_id])
    specialty = relationship("Specialty", foreign_keys=[specialty_id])

    __table_args__ = (
        CheckConstraint(
            "failure_threshold >= 0.0 AND failure_threshold <= 1.0",
            name="ck_plan_failure_threshold",
        ),
        CheckConstraint(
            "next_plan_id IS NULL OR next_plan_id <> id",
            name="ck_plan_no_self_chain",
        ),
        Index("idx_plan_next_plan", "next_plan_id"),
        Index("idx_plan_project", "project_id"),
    )


class PlanStep(Base):
    __tablename__ = "plan_step"

    id              = Column(Integer, primary_key=True)
    plan_id         = Column(Integer, ForeignKey("plan.id", ondelete="CASCADE"), nullable=False)
    step_key        = Column(String(256), nullable=False)
    script_name     = Column(String(128), nullable=False)
    script_version  = Column(String(32), nullable=False)
    stage           = Column(String(32), nullable=False)
    sort_order      = Column(Integer, nullable=False, default=0)
    timeout_seconds = Column(Integer, nullable=True)
    # 停滞钟（#115 阶段 1）：多久无 PROGRESS 戳算卡死。NULL/0 = 关闭（缺省）。
    # 与 timeout_seconds 不同，0 是合法且有意义的（= 不启用）。启用的前提是
    # 该步骤脚本已接入 PROGRESS 打戳（#115 阶段 2）。
    stall_seconds   = Column(Integer, nullable=True)
    retry           = Column(Integer, nullable=False, default=0)
    enabled         = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at      = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    plan = relationship("Plan", foreign_keys=[plan_id], back_populates="steps")

    __table_args__ = (
        CheckConstraint(
            "stage IN ('init', 'patrol', 'teardown')",
            name="ck_plan_step_stage",
        ),
        UniqueConstraint("plan_id", "step_key", name="uq_plan_step_key"),
        Index("idx_plan_step_plan_stage_order", "plan_id", "stage", "sort_order"),
    )
