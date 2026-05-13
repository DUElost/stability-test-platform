"""DeviceLease ORM — ADR-0019 Phase 1.

A device_lease represents exclusive occupancy of an Android device for the
duration of a job, a script execution, or a maintenance window.  Only one
ACTIVE lease may exist per device at any time (enforced by a PostgreSQL
partial unique index created in the Alembic migration).

The fencing_token is generated from device.lease_generation, which is
atomically incremented on each acquire.  This module stores a snapshot of
the generation value at the time the lease was created.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import relationship

from backend.core.database import Base


class DeviceLease(Base):
    __tablename__ = "device_leases"

    id                 = Column(Integer, primary_key=True)
    device_id          = Column(Integer, ForeignKey("device.id"), nullable=False)
    job_id             = Column(Integer, ForeignKey("job_instance.id"), nullable=True)
    host_id            = Column(String(64), ForeignKey("host.id"), nullable=False)
    lease_type         = Column(String(32), nullable=False)
    status             = Column(String(32), nullable=False)
    fencing_token      = Column(String(256), nullable=False)
    lease_generation   = Column(Integer, nullable=False)
    agent_instance_id  = Column(String(64), nullable=False)
    reason             = Column(String(256), nullable=True)
    holder             = Column(String(128), nullable=True)
    acquired_at        = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    renewed_at         = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    expires_at         = Column(DateTime(timezone=True), nullable=False)
    released_at        = Column(DateTime(timezone=True), nullable=True)

    device = relationship("backend.models.host.Device", foreign_keys=[device_id])
    job    = relationship("backend.models.job.JobInstance", foreign_keys=[job_id])
    host   = relationship("backend.models.host.Host", foreign_keys=[host_id])

    # Partial unique index: at most one ACTIVE lease per device (ADR-0019).
    # SQLAlchemy 1.4+ supports partial indexes via ``postgresql_where``,
    # so the constraint is now the single source of truth in the ORM and is
    # picked up by both Base.metadata.create_all() (test DBs) and Alembic
    # autogenerate.  The historical raw-SQL migration is still the source for
    # legacy production databases.
    __table_args__ = (
        Index("idx_device_leases_host",          "host_id"),
        Index("idx_device_leases_device_status", "device_id", "status"),
        Index("idx_device_leases_expires",       "expires_at"),
        Index(
            "uq_device_leases_active_per_device",
            "device_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )
