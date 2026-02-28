from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB

from backend.core.database import Base


class Host(Base):
    __tablename__ = "host"

    id                   = Column(String(64), primary_key=True)
    hostname             = Column(String(256), nullable=False)
    ip_address           = Column(String(64))
    tool_catalog_version = Column(String(64))
    last_heartbeat       = Column(DateTime(timezone=True))
    cpu_quota            = Column(Integer, nullable=False, default=2)
    status               = Column(String(32), nullable=False, default="OFFLINE")
    created_at           = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class Device(Base):
    __tablename__ = "device"

    id         = Column(Integer, primary_key=True)
    serial     = Column(String(128), nullable=False, unique=True)
    host_id    = Column(String(64), ForeignKey("host.id"))
    model      = Column(String(128))
    platform   = Column(String(64))
    tags       = Column(JSONB, nullable=False, default=dict)
    status     = Column(String(32), nullable=False, default="OFFLINE")
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_device_host", "host_id"),
    )
