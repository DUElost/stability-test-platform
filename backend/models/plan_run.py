"""PlanRun ORM — ADR-0020.

Every execution of a Plan (manual, cron, or chain-triggered) produces one
PlanRun.  Multi-Plan chains produce one PlanRun per segment, linked by
parent_plan_run_id / root_plan_run_id.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum as SAEnum,
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
from backend.models.enums import PlanRunStatus

PLAN_RUN_STATUS_DB_ENUM = SAEnum(
    *(status.value for status in PlanRunStatus),
    name="plan_run_status",
    validate_strings=True,
)


class PlanRun(Base):
    __tablename__ = "plan_run"

    id                = Column(Integer, primary_key=True)
    plan_id           = Column(Integer, ForeignKey("plan.id"), nullable=False)
    status            = Column(PLAN_RUN_STATUS_DB_ENUM, nullable=False, default=PlanRunStatus.RUNNING.value)
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
    chain_index         = Column(Integer, nullable=False, default=0, server_default="0")
    next_plan_triggered = Column(Boolean, nullable=False, default=False, server_default="false")

    plan = relationship("Plan", foreign_keys=[plan_id],
                        back_populates="runs")
    jobs = relationship("backend.models.job.JobInstance",
                        back_populates="plan_run", lazy="dynamic")

    __table_args__ = (
        CheckConstraint(
            "failure_threshold >= 0.0 AND failure_threshold <= 1.0",
            name="ck_plan_run_failure_threshold",
        ),
        CheckConstraint(
            "run_type IN ('MANUAL','SCHEDULE','CHAIN')",
            name="ck_plan_run_type",
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
