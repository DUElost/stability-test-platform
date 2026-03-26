from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

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

    definition = relationship(
        "backend.models.workflow.WorkflowDefinition",
        foreign_keys=[workflow_definition_id],
        back_populates="task_templates",
    )
    jobs = relationship("JobInstance", back_populates="task_template", lazy="dynamic")


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
    started_at         = Column(DateTime(timezone=True))
    ended_at           = Column(DateTime(timezone=True))
    report_json        = Column(JSONB)
    jira_draft_json    = Column(JSONB)
    post_processed_at  = Column(DateTime(timezone=True))
    created_at         = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at         = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    workflow_run   = relationship("backend.models.workflow.WorkflowRun", foreign_keys=[workflow_run_id], back_populates="jobs")
    task_template  = relationship("TaskTemplate", foreign_keys=[task_template_id], back_populates="jobs")
    device         = relationship("backend.models.host.Device", foreign_keys=[device_id])
    host           = relationship("backend.models.host.Host", foreign_keys=[host_id])
    step_traces    = relationship("StepTrace", back_populates="job", lazy="dynamic")
    artifacts      = relationship("JobArtifact", back_populates="job", lazy="dynamic")

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

    job = relationship("JobInstance", foreign_keys=[job_id], back_populates="step_traces")

    __table_args__ = (
        UniqueConstraint("job_id", "step_id", "event_type", name="uq_step_trace_idempotent"),
        Index("idx_step_trace_job", "job_id"),
    )


class JobArtifact(Base):
    """Artifact produced by a JobInstance (logs, reports, crash dumps)."""
    __tablename__ = "job_artifact"

    id          = Column(Integer, primary_key=True)
    job_id      = Column(Integer, ForeignKey("job_instance.id"), nullable=False)
    storage_uri = Column(String(512), nullable=False)
    artifact_type = Column(String(64), nullable=False, default="log")
    size_bytes  = Column(BigInteger)
    checksum    = Column(String(128))
    created_at  = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    job = relationship("JobInstance", foreign_keys=[job_id], back_populates="artifacts")

    __table_args__ = (
        Index("idx_job_artifact_job", "job_id"),
    )
