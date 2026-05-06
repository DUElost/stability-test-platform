"""Plan ORM — ADR-0020.

Plan replaces WorkflowDefinition as the top-level orchestration unit.
Each Plan maps 1:1 to a former TaskTemplate; multi-template workflows
become Plan chains via next_plan_id.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
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
    lifecycle         = Column(JSONB, nullable=False)
    next_plan_id      = Column(Integer, ForeignKey("plan.id"), nullable=True)
    watcher_policy    = Column(JSONB, nullable=True)
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

    __table_args__ = (
        CheckConstraint(
            "next_plan_id IS NULL OR next_plan_id <> id",
            name="ck_plan_no_self_chain",
        ),
        Index("idx_plan_next_plan", "next_plan_id"),
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
    retry           = Column(Integer, nullable=False, default=0)
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
