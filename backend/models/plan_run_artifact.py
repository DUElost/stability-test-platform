"""PlanRunArtifact — PlanRun 维度的产物（ADR-0025 Sprint 4 归档-2/3）。

与 JobArtifact（Job 维度）解耦：scan/merge 产物不属于某个 Job，
而是 PlanRun 维度的去重/合并结果（Result_*.xls）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)

from backend.core.database import Base


class PlanRunArtifact(Base):
    """PlanRun 维度产物（scan_result_xls / merge_result_xls / extract_bundle）。

    幂等键：(plan_run_id, storage_uri) —— 重复 scan/merge 不产生重复行。
    """
    __tablename__ = "plan_run_artifact"

    id            = Column(Integer, primary_key=True)
    plan_run_id   = Column(Integer, ForeignKey("plan_run.id", ondelete="CASCADE"), nullable=False)
    host_id       = Column(String(128), nullable=True)
    storage_uri   = Column(String(512), nullable=False)
    artifact_type = Column(String(64), nullable=False, default="scan_result_xls")
    size_bytes    = Column(BigInteger)
    created_at    = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("plan_run_id", "storage_uri", name="uq_plan_run_artifact_run_storage"),
        Index("idx_plan_run_artifact_run", "plan_run_id"),
    )
