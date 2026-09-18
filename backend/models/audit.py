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
        # #2694：`action` 原先无索引，而 facets 对它做 `group_by(action)` 聚合——生产
        # 实测该表已 266,882 行、86+ 种 action，每次开 /audit 都在无索引列上全表聚合。
        # 复合列序 `(action, timestamp)`：既供 action 的精确筛选/聚合（前导列），
        # 也让「按 action 过滤 + 按时间倒序」这一最常见组合走索引序（免排序）。
        Index("ix_audit_action_ts", "action", "timestamp"),
        # `timestamp` 单列：列表默认 `order_by(timestamp.desc())`；不带 user 过滤时
        # 走不到 `ix_audit_user_ts`（前导列是 user_id）⇒ 退化为全表排序。
        Index("ix_audit_ts", "timestamp"),
    )

    id            = Column(Integer, primary_key=True)
    user_id       = Column(Integer, ForeignKey("users.id"), nullable=True)
    username      = Column(String(128))
    action        = Column(String(64), nullable=False)
    resource_type = Column(String(64), nullable=False)
    resource_id   = Column(String(64))
    details       = Column(JSON, default=dict)
    ip_address    = Column(String(64))
    timestamp     = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
