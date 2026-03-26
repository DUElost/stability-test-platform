from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String

from backend.core.database import Base


class TaskSchedule(Base):
    """Cron-based task scheduling."""
    __tablename__ = "task_schedules"
    __table_args__ = (
        Index("ix_sched_enabled_next", "enabled", "next_run_at"),
    )

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    cron_expression = Column(String(128), nullable=False)
    task_template_id = Column(Integer, nullable=True)
    tool_id = Column(Integer, nullable=True)
    task_type = Column(String(32), nullable=False)
    params = Column(JSON, default=dict)
    target_device_id = Column(Integer, nullable=True)
    enabled = Column(Boolean, default=True)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    workflow_definition_id = Column(Integer, ForeignKey("workflow_definition.id"), nullable=True)
    device_ids = Column(JSON, nullable=True)
