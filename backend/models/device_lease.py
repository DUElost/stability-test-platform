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

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String
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
    acquired_at        = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    renewed_at         = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    expires_at         = Column(DateTime(timezone=True), nullable=False)
    released_at        = Column(DateTime(timezone=True), nullable=True)

    device = relationship("backend.models.host.Device", foreign_keys=[device_id])
    job    = relationship("backend.models.job.JobInstance", foreign_keys=[job_id])
    host   = relationship("backend.models.host.Host", foreign_keys=[host_id])

    # Partial unique index uq_device_leases_active_per_device is created in
    # the Alembic migration via op.execute() — it cannot be declared here
    # because SQLAlchemy __table_args__ does not support WHERE on Index.
    __table_args__ = (
        Index("idx_device_leases_host",          "host_id"),
        Index("idx_device_leases_device_status", "device_id", "status"),
        Index("idx_device_leases_expires",       "expires_at"),
    )
