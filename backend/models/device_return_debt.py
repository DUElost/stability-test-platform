"""DeviceReturnDebt ORM — ADR-0061 D5 / #3723 U3.

One open row per (device, family, source_kind, source_job_id) records an unpaid
return obligation. All open rows on a device share one MAINTENANCE debt lease.

The partial unique index ``uq_device_return_debt_open_source`` uses
``COALESCE(source_job_id, 0)`` so NULL job ids still collide; it must stay in
``__table_args__`` so schema-sync matches the Alembic revision.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from backend.core.database import Base


class DeviceReturnDebt(Base):
    __tablename__ = "device_return_debt"

    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("device.id", ondelete="CASCADE"), nullable=False)
    family = Column(String(64), nullable=False)
    source_kind = Column(String(32), nullable=False)  # job | stock
    source_job_id = Column(
        Integer, ForeignKey("job_instance.id", ondelete="SET NULL"), nullable=True
    )
    source_fencing_token = Column(String(256), nullable=True)
    cause = Column(String(64), nullable=False)
    state = Column(String(32), nullable=False)  # open | closed
    closed_reason = Column(String(32), nullable=True)  # discharged | overridden
    closed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    evidence = Column(JSONB, nullable=True)

    device = relationship("backend.models.host.Device", foreign_keys=[device_id])
    source_job = relationship(
        "backend.models.job.JobInstance", foreign_keys=[source_job_id]
    )

    __table_args__ = (
        Index("ix_device_return_debt_device_state", "device_id", "state"),
        Index("ix_device_return_debt_family_state", "family", "state"),
        Index(
            "uq_device_return_debt_open_source",
            "device_id",
            "family",
            "source_kind",
            text("COALESCE(source_job_id, 0)"),
            unique=True,
            postgresql_where=text("state = 'open'"),
        ),
    )
