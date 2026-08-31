"""DeviceLogEvent — 设备日志事件权威记录（ADR-0028 D1）。"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.core.database import Base


class DeviceLogEvent(Base):
    __tablename__ = "device_log_event"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    serial = Column(String(128), nullable=False)
    platform = Column(String(16), nullable=False)
    event_type = Column(String(32), nullable=False)
    event_subtype = Column(String(128), nullable=True)
    detected_at = Column(DateTime(timezone=True), nullable=False)
    device_timestamp = Column(DateTime(timezone=True), nullable=True)
    state = Column(String(32), nullable=False, default="DETECTED")
    local_path = Column(String(1024), nullable=False)
    remote_path = Column(String(1024), nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    checksum = Column(String(64), nullable=True)
    plan_run_id = Column(
        Integer,
        ForeignKey("plan_run.id", ondelete="SET NULL"),
        nullable=True,
    )
    host_id = Column(
        String(64),
        ForeignKey("host.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    job_id = Column(
        Integer,
        ForeignKey("job_instance.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Agent outbox seq_no; used to reverse-link job_log_signal after late ingest (#214).
    signal_seq_no = Column(BigInteger, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    host = relationship("Host", foreign_keys=[host_id])
    job = relationship("JobInstance", foreign_keys=[job_id])
    plan_run = relationship("PlanRun", foreign_keys=[plan_run_id])
    log_signals = relationship(
        "JobLogSignal",
        back_populates="device_log_event",
        foreign_keys="JobLogSignal.device_log_event_id",
    )

    __table_args__ = (
        Index("idx_device_log_event_plan_state", "plan_run_id", "state"),
        Index("idx_device_log_event_host_state_detected", "host_id", "state", "detected_at"),
        Index("idx_device_log_event_serial_detected", "serial", "detected_at"),
        Index("idx_device_log_event_state_updated", "state", "updated_at"),
        Index("idx_device_log_event_job_signal_seq", "job_id", "signal_seq_no"),
    )
