"""
Legacy ORM models — mapped to tables scheduled for DROP.

These models exist solely to support legacy API endpoints that still read
from the old table structure (tasks, task_runs, run_steps, log_artifacts,
tools, tool_categories, task_templates).  Once all API consumers migrate
to the new orchestration layer, these models and their tables can be
removed entirely.

Canonical replacements:
  task_templates  → task_template  (backend.models.job.TaskTemplate)
  tasks           → workflow_definition + job_instance
  task_runs       → job_instance   (backend.models.job.JobInstance)
  run_steps       → step_trace     (backend.models.job.StepTrace)
  log_artifacts   → job_artifact   (backend.models.job.JobArtifact)
  tool_categories → tool.category  (backend.models.tool.Tool)
  tools           → tool           (backend.models.tool.Tool)
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from backend.core.database import Base
from backend.models.enums import RunStatus, RunStepStatus, TaskStatus


class LegacyTaskTemplate(Base):
    __tablename__ = "task_templates"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), unique=True, nullable=False)
    type = Column(String(32), nullable=False)
    description = Column(String(256))
    default_params = Column(JSON, default=dict)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    tasks = relationship("Task", back_populates="template")


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_task_status", "status"),
    )

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    type = Column(String(32), nullable=False)
    template_id = Column(Integer, ForeignKey("task_templates.id"))
    tool_id = Column(Integer, ForeignKey("tools.id"), nullable=True)
    params = Column(JSON, default=dict)
    tool_snapshot = Column(JSON, nullable=True)
    target_device_id = Column(Integer, ForeignKey("devices.id"))
    status = Column(Enum(TaskStatus), default=TaskStatus.PENDING, nullable=False)
    priority = Column(Integer, default=0)

    group_id = Column(String(32), index=True)
    is_distributed = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    pipeline_def = Column(JSON, nullable=True)

    template = relationship("LegacyTaskTemplate", back_populates="tasks")
    tool = relationship("LegacyTool")
    runs = relationship("TaskRun", back_populates="task")


class TaskRun(Base):
    __tablename__ = "task_runs"
    __table_args__ = (
        Index("ix_tr_host_status", "host_id", "status"),
        Index("ix_tr_task_id", "task_id"),
        Index("ix_tr_status", "status"),
    )

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    host_id = Column(Integer, ForeignKey("hosts.id"), nullable=False)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False)
    status = Column(Enum(RunStatus), default=RunStatus.QUEUED, nullable=False)

    group_id = Column(String(32), index=True)

    progress = Column(Integer, default=0)
    progress_message = Column(String(256))

    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    last_heartbeat_at = Column(DateTime)
    exit_code = Column(Integer)
    error_code = Column(String(64))
    error_message = Column(Text)
    log_summary = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    report_json = Column(JSON, nullable=True)
    jira_draft_json = Column(JSON, nullable=True)
    post_processed_at = Column(DateTime, nullable=True)

    task = relationship("Task", back_populates="runs")
    artifacts = relationship("LogArtifact", back_populates="run")
    steps = relationship(
        "RunStep",
        back_populates="run",
        order_by="RunStep.phase, RunStep.step_order",
        cascade="all, delete-orphan",
    )


class RunStep(Base):
    __tablename__ = "run_steps"
    __table_args__ = (
        Index("ix_rs_run_id", "run_id"),
        Index("ix_rs_run_status", "run_id", "status"),
    )

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("task_runs.id", ondelete="CASCADE"), nullable=False)
    phase = Column(String(64), nullable=False)
    step_order = Column(Integer, nullable=False)
    name = Column(String(128), nullable=False)
    action = Column(String(256), nullable=False)
    params = Column(JSON, default=dict)
    status = Column(Enum(RunStepStatus), default=RunStepStatus.PENDING, nullable=False)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    exit_code = Column(Integer)
    error_message = Column(Text)
    log_line_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    run = relationship("TaskRun", back_populates="steps")


class LogArtifact(Base):
    __tablename__ = "log_artifacts"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("task_runs.id"), nullable=False, index=True)
    storage_uri = Column(String(512), nullable=False)
    size_bytes = Column(BigInteger)
    checksum = Column(String(128))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    run = relationship("TaskRun", back_populates="artifacts")


class LegacyToolCategory(Base):
    __tablename__ = "tool_categories"

    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False, unique=True)
    description = Column(String(256))
    icon = Column(String(32))
    order = Column(Integer, default=0)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    tools = relationship("LegacyTool", back_populates="category")


class LegacyTool(Base):
    __tablename__ = "tools"

    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, ForeignKey("tool_categories.id"), nullable=False, index=True)

    name = Column(String(128), nullable=False)
    description = Column(String(256))

    script_path = Column(String(512), nullable=False)
    script_class = Column(String(128))
    script_type = Column(String(16), default="python")

    default_params = Column(JSON, default=dict)
    param_schema = Column(JSON, default=dict)

    timeout = Column(Integer, default=3600)
    need_device = Column(Boolean, default=True)

    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    category = relationship("LegacyToolCategory", back_populates="tools")
