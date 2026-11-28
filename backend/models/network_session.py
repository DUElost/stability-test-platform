"""DeviceNetworkSession ORM — ADR-0066 D2.2 / B13 U1.

Single authoritative ledger for managed network-supply sessions (``wifi`` and
``usb_net``), spanning both ``job`` (three-point lease handover) and ``manual``
(admin debug) entry paths.

Database invariants enforced by PostgreSQL constraints & partial indexes:
- ``ck_device_network_session_lease_binding`` (ADR-0066 D2.2):
  ``lease_id IS NULL AND generation IS NULL`` is permitted ONLY in
  ``source = 'job' AND status = 'RESERVED'`` or
  ``source = 'job' AND status = 'RELEASED' AND release_reason = 'unclaimed_canceled'``;
  every other row MUST bind both ``lease_id`` and ``generation``.
- ``uq_device_network_session_active_per_device`` (ADR-0066 D2.3):
  at most one unreleased session per device at any time.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from backend.core.database import Base

ACTIVE_NETWORK_SESSION_STATUSES = (
    "RESERVED",
    "PROVISIONING",
    "ACTIVE",
    "RELEASING",
    "RELEASE_FAILED",
)

ALL_NETWORK_SESSION_STATUSES = (
    *ACTIVE_NETWORK_SESSION_STATUSES,
    "RELEASED",
)

VALID_NETWORK_SESSION_SOURCES = ("job", "manual")
VALID_NETWORK_RELEASE_REASONS = (
    "teardown_confirmed",
    "unclaimed_canceled",
    "late_confirmed",
    "debt_discharged",
    "manual_released",
    "manual_override",
)


class DeviceNetworkSession(Base):
    __tablename__ = "device_network_session"

    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("device.id"), nullable=False)
    host_id = Column(
        String(64),
        ForeignKey("host.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    resource_pool_id = Column(
        Integer, ForeignKey("resource_pool.id"), nullable=False
    )
    resource_type = Column(String(32), nullable=False)
    egress = Column(
        String(32),
        nullable=False,
        default="direct",
        server_default=text("'direct'"),
    )
    source = Column(String(32), nullable=False)
    plan_run_id = Column(Integer, ForeignKey("plan_run.id"), nullable=True)
    job_instance_id = Column(
        Integer, ForeignKey("job_instance.id"), nullable=True
    )
    lease_id = Column(Integer, ForeignKey("device_leases.id"), nullable=True)
    generation = Column(Integer, nullable=True)
    legacy_path = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    status = Column(String(32), nullable=False)
    release_reason = Column(String(64), nullable=True)
    assigned_ip = Column(String(64), nullable=True)
    usb_devpath = Column(String(128), nullable=True)
    prior_network_snapshot = Column(JSONB, nullable=True)
    created_by = Column(String(128), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
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
    released_at = Column(DateTime(timezone=True), nullable=True)

    device = relationship(
        "backend.models.host.Device", foreign_keys=[device_id]
    )
    host = relationship("backend.models.host.Host", foreign_keys=[host_id])
    pool = relationship(
        "backend.models.resource_pool.ResourcePool",
        foreign_keys=[resource_pool_id],
    )
    plan_run = relationship(
        "backend.models.plan_run.PlanRun", foreign_keys=[plan_run_id]
    )
    job = relationship(
        "backend.models.job.JobInstance", foreign_keys=[job_instance_id]
    )
    lease = relationship(
        "backend.models.device_lease.DeviceLease", foreign_keys=[lease_id]
    )

    __table_args__ = (
        CheckConstraint(
            "(lease_id IS NOT NULL AND generation IS NOT NULL) OR ("
            "lease_id IS NULL AND generation IS NULL AND source = 'job' AND ("
            "status = 'RESERVED' OR "
            "(status = 'RELEASED' AND release_reason = 'unclaimed_canceled')"
            "))",
            name="ck_device_network_session_lease_binding",
        ),
        CheckConstraint(
            "(source = 'job' AND job_instance_id IS NOT NULL) OR "
            "(source = 'manual' AND job_instance_id IS NULL AND expires_at IS NOT NULL)",
            name="ck_device_network_session_source_fields",
        ),
        CheckConstraint(
            "status IN ('RESERVED', 'PROVISIONING', 'ACTIVE', 'RELEASING', 'RELEASE_FAILED', 'RELEASED')",
            name="ck_device_network_session_status",
        ),
        CheckConstraint(
            "resource_type IN ('wifi', 'usb_net')",
            name="ck_device_network_session_resource_type",
        ),
        CheckConstraint(
            "egress IN ('direct', 'vpn')",
            name="ck_device_network_session_egress",
        ),
        Index(
            "uq_device_network_session_active_per_device",
            "device_id",
            unique=True,
            postgresql_where=text(
                "status IN ('RESERVED', 'PROVISIONING', 'ACTIVE', 'RELEASING', 'RELEASE_FAILED')"
            ),
        ),
        Index("ix_device_network_session_host_status", "host_id", "status"),
        Index(
            "ix_device_network_session_pool_status",
            "resource_pool_id",
            "status",
        ),
        Index("ix_device_network_session_job", "job_instance_id"),
    )
