from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from backend.core.database import Base


class WorkflowDefinition(Base):
    __tablename__ = "workflow_definition"

    id                = Column(Integer, primary_key=True)
    name              = Column(String(256), nullable=False)
    description       = Column(Text)
    failure_threshold = Column(Float, nullable=False, default=0.05)
    created_by        = Column(String(128))
    # Watcher 策略覆盖：运维可针对特定 WorkflowDefinition 覆盖 Agent 默认 WatcherPolicy
    # 字段形态参考 backend/agent/watcher/policy.py WatcherPolicy.from_job
    # 可选字段：on_unavailable / required_categories / paths / nfs_quota_mb / ...
    watcher_policy    = Column(JSONB, nullable=True)
    created_at        = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at        = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    task_templates = relationship("backend.models.job.TaskTemplate", back_populates="definition", lazy="dynamic")
    runs           = relationship("WorkflowRun", back_populates="definition", lazy="dynamic")


class WorkflowRun(Base):
    __tablename__ = "workflow_run"

    id                     = Column(Integer, primary_key=True)
    workflow_definition_id = Column(Integer, ForeignKey("workflow_definition.id"), nullable=False)
    status                 = Column(String(32), nullable=False, default="RUNNING")
    failure_threshold      = Column(Float, nullable=False, default=0.05)
    triggered_by           = Column(String(128))
    started_at             = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    ended_at               = Column(DateTime(timezone=True))
    result_summary         = Column(JSONB)

    definition = relationship("WorkflowDefinition", foreign_keys=[workflow_definition_id], back_populates="runs")
    jobs       = relationship("backend.models.job.JobInstance", back_populates="workflow_run", lazy="dynamic")
