"""PlanRun ORM — ADR-0020.

PlanRun replaces WorkflowRun.  Every execution of a Plan (manual, cron, or
chain-triggered) produces one PlanRun.  Multi-Plan chains produce one PlanRun
per segment, linked by parent_plan_run_id / root_plan_run_id.
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from backend.core.database import Base


class PlanRun(Base):
    __tablename__ = "plan_run"

    id                = Column(Integer, primary_key=True)
    plan_id           = Column(Integer, ForeignKey("plan.id"), nullable=False)
    status            = Column(String(32), nullable=False, default="RUNNING")
    failure_threshold = Column(Float, nullable=False, default=0.05)
    plan_snapshot     = Column(JSONB, nullable=False)
    run_type          = Column(String(16), nullable=False)
    run_context       = Column(JSONB, nullable=True)
    triggered_by      = Column(String(128))
    started_at        = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    ended_at          = Column(DateTime(timezone=True))
    result_summary    = Column(JSONB)

    parent_plan_run_id  = Column(Integer, ForeignKey("plan_run.id"), nullable=True)
    root_plan_run_id    = Column(Integer, ForeignKey("plan_run.id"), nullable=True)
    chain_index         = Column(Integer, nullable=True)
    next_plan_triggered = Column(String(1), nullable=False, default="0")

    plan = relationship("Plan", foreign_keys=[plan_id],
                        back_populates="runs")
    jobs = relationship("backend.models.job.JobInstance",
                        back_populates="plan_run", lazy="dynamic")

    __table_args__ = (
        CheckConstraint(
            "run_type IN ('MANUAL','SCHEDULE','CHAIN')",
            name="ck_plan_run_type",
        ),
        CheckConstraint(
            "next_plan_triggered IN ('0','1')",
            name="ck_plan_run_next_triggered",
        ),
        Index("idx_plan_run_plan", "plan_id"),
        Index("idx_plan_run_status", "status"),
        Index("idx_plan_run_parent", "parent_plan_run_id"),
        Index("idx_plan_run_root", "root_plan_run_id"),
        Index(
            "uniq_plan_run_chain_child",
            "parent_plan_run_id",
            "plan_id",
            unique=True,
            postgresql_where=text("parent_plan_run_id IS NOT NULL"),
        ),
    )
