"""TestCaseResult ORM — ADR-0030 P2 逐条用例结果。

数据源 = 中心存储 ``mtbf/{project}/results/{run_dir}.json``（``mtbf_finish``
产出）。按 Job 摄入、PlanRun 维度查询。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from backend.core.database import Base


class TestCaseResult(Base):
    __test__ = False
    __tablename__ = "test_case_result"

    id = Column(Integer, primary_key=True)
    plan_run_id = Column(Integer, ForeignKey("plan_run.id", ondelete="CASCADE"), nullable=False)
    job_id = Column(Integer, ForeignKey("job_instance.id", ondelete="CASCADE"), nullable=False)
    suite_id = Column(Integer, ForeignKey("test_suite.id", ondelete="SET NULL"), nullable=True)
    case_id = Column(Integer, ForeignKey("test_case.id", ondelete="SET NULL"), nullable=True)
    case_name = Column(String(512), nullable=False)
    status = Column(String(32), nullable=False)  # PASS / FAILURE / ERROR
    detail = Column(Text, nullable=True)
    artifact_uri = Column(String(1024), nullable=True)
    run_dir = Column(String(256), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    plan_run = relationship("PlanRun", foreign_keys=[plan_run_id])
    job = relationship("JobInstance", foreign_keys=[job_id])
    suite = relationship("TestSuite", foreign_keys=[suite_id])
    case = relationship("TestCase", foreign_keys=[case_id])

    __table_args__ = (
        UniqueConstraint("job_id", "case_name", name="uq_test_case_result_job_case"),
        Index("idx_test_case_result_plan_run", "plan_run_id"),
        Index("idx_test_case_result_job", "job_id"),
    )
