from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String  # noqa: F401

from backend.core.database import Base


def schedule_timestamp(value: datetime) -> datetime:
    """Return UTC-naive datetime for task_schedules timestamp columns."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _utcnow_schedule_timestamp(ctx=None) -> datetime:
    return schedule_timestamp(datetime.now(timezone.utc))


class TaskSchedule(Base):
    """Cron-based Plan scheduling (ADR-0020).

    ADR-0020 §Phase 5 收紧：本表只触发 Plan，不再承担参数 override 与单设备字段。
    """
    __tablename__ = "task_schedules"
    __table_args__ = (
        Index("ix_sched_enabled_next", "enabled", "next_run_at"),
    )

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    cron_expression = Column(String(128), nullable=False)
    plan_id = Column(Integer, ForeignKey("plan.id"), nullable=False)
    enabled = Column(Boolean, default=True)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=_utcnow_schedule_timestamp, nullable=False)
    device_ids = Column(JSON, nullable=True)
