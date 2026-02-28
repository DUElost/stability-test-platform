from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from backend.core.database import Base


class TaskTemplate(Base):
    __tablename__ = "task_template"

    id                     = Column(Integer, primary_key=True)
    workflow_definition_id = Column(Integer, ForeignKey("workflow_definition.id", ondelete="CASCADE"), nullable=False)
    name                   = Column(String(256), nullable=False)
    pipeline_def           = Column(JSONB, nullable=False)
    platform_filter        = Column(JSONB)
    sort_order             = Column(Integer, nullable=False, default=0)
    created_at             = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class JobInstance(Base):
    __tablename__ = "job_instance"

    id               = Column(Integer, primary_key=True)
    workflow_run_id  = Column(Integer, ForeignKey("workflow_run.id"), nullable=False)
    task_template_id = Column(Integer, ForeignKey("task_template.id"), nullable=False)
    device_id        = Column(Integer, ForeignKey("device.id"), nullable=False)
    host_id          = Column(String(64), ForeignKey("host.id"))
    status           = Column(String(32), nullable=False, default="PENDING")
    status_reason    = Column(Text)
    pipeline_def     = Column(JSONB, nullable=False)
    started_at       = Column(DateTime(timezone=True))
    ended_at         = Column(DateTime(timezone=True))
    created_at       = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at       = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_job_instance_status",   "status"),
        Index("idx_job_instance_workflow",  "workflow_run_id"),
        Index("idx_job_instance_host",      "host_id"),
    )


class StepTrace(Base):
    __tablename__ = "step_trace"

    id            = Column(Integer, primary_key=True)
    job_id        = Column(Integer, ForeignKey("job_instance.id"), nullable=False)
    step_id       = Column(String(128), nullable=False)
    stage         = Column(String(32), nullable=False)
    status        = Column(String(32), nullable=False)
    event_type    = Column(String(32), nullable=False)
    output        = Column(Text)
    error_message = Column(Text)
    original_ts   = Column(DateTime(timezone=True), nullable=False)
    created_at    = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("job_id", "step_id", "event_type", name="uq_step_trace_idempotent"),
        Index("idx_step_trace_job", "job_id"),
    )
