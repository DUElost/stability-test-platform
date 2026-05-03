from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, String

from backend.core.database import Base


class AuditLog(Base):
    """Audit log for tracking mutation operations.

    Keeps ``__tablename__ = "audit_logs"`` (plural) to match the existing
    production table and avoid a risky rename migration.
    """
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_user_ts", "user_id", "timestamp"),
        Index("ix_audit_resource", "resource_type", "resource_id"),
    )

    id            = Column(Integer, primary_key=True)
    user_id       = Column(Integer, ForeignKey("users.id"), nullable=True)
    username      = Column(String(128))
    action        = Column(String(64), nullable=False)
    resource_type = Column(String(64), nullable=False)
    resource_id   = Column(Integer)
    details       = Column(JSON, default=dict)
    ip_address    = Column(String(64))
    timestamp     = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
