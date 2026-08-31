"""Resource pool models: WiFi router pools and per-device allocations."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from backend.core.database import Base


class ResourcePool(Base):
    __tablename__ = "resource_pool"

    id                     = Column(Integer, primary_key=True)
    name                   = Column(String(256), nullable=False)
    resource_type          = Column(String(32), nullable=False, default="wifi")
    config                 = Column(JSONB, nullable=False, default=dict)
    # config = {
    #     "ssid": "LabWiFi-2.4G",
    #     "password": "secret",
    #     "router_ip": "192.0.2.1",
    #     "band": "2.4g" | "5g",
    # }
    max_concurrent_devices = Column(Integer, nullable=False, default=30)
    host_group             = Column(String(128), nullable=True)
    is_active              = Column(Boolean, nullable=False, default=True)
    created_at             = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at             = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    allocations = relationship("ResourceAllocation", back_populates="pool", lazy="dynamic")


class ResourceAllocation(Base):
    __tablename__ = "resource_allocation"

    id              = Column(Integer, primary_key=True)
    job_instance_id = Column(Integer, ForeignKey("job_instance.id"), nullable=False)
    resource_pool_id = Column(Integer, ForeignKey("resource_pool.id"), nullable=False)
    device_id       = Column(Integer, ForeignKey("device.id"), nullable=False)
    allocated_params = Column(JSONB, nullable=False, default=dict)
    # allocated_params = {"ssid": "LabWiFi-2.4G", "password": "secret"}
    created_at      = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    pool = relationship("ResourcePool", foreign_keys=[resource_pool_id], back_populates="allocations")
    job  = relationship("JobInstance", foreign_keys=[job_instance_id])
    device = relationship("Device", foreign_keys=[device_id])

    __table_args__ = (
        # 对齐迁移链既有索引（schema-sync 基线收敛）
        Index("ix_resource_allocation_job", "job_instance_id"),
        Index("ix_resource_allocation_pool", "resource_pool_id"),
    )
