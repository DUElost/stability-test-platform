from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from backend.core.database import Base


class ChannelType(str, PyEnum):
    WEBHOOK = "WEBHOOK"
    EMAIL = "EMAIL"
    DINGTALK = "DINGTALK"


class EventType(str, PyEnum):
    RUN_COMPLETED = "RUN_COMPLETED"
    RUN_FAILED = "RUN_FAILED"
    RISK_HIGH = "RISK_HIGH"
    DEVICE_OFFLINE = "DEVICE_OFFLINE"


class NotificationSource(str, PyEnum):
    PLATFORM = "PLATFORM"
    ALERTMANAGER = "ALERTMANAGER"


class NotificationSeverity(str, PyEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class NotificationChannel(Base):
    __tablename__ = "notification_channels"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    type = Column(Enum(ChannelType), nullable=False)
    config = Column(JSON, default=dict)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    rules = relationship("AlertRule", back_populates="channel")


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    event_type = Column(Enum(EventType), nullable=False)
    channel_id = Column(Integer, ForeignKey("notification_channels.id"), nullable=False)
    filters = Column(JSON, default=dict)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    channel = relationship("NotificationChannel", back_populates="rules")


class NotificationLog(Base):
    __tablename__ = "notification_logs"

    id = Column(Integer, primary_key=True)
    source = Column(Enum(NotificationSource, values_callable=lambda e: [m.value for m in e]), nullable=False, default=NotificationSource.PLATFORM)
    event_type = Column(String(64), nullable=False)
    severity = Column(Enum(NotificationSeverity, values_callable=lambda e: [m.value for m in e]), nullable=False, default=NotificationSeverity.INFO)
    title = Column(String(256), nullable=False)
    message = Column(Text, nullable=False, default="")
    context = Column(JSON, default=dict)
    read = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        # 对齐迁移链既有索引（schema-sync 基线收敛）
        Index("ix_notification_logs_created_at", "created_at"),
        Index("ix_notification_logs_read", "read"),
    )


class NotificationDelivery(Base):
    """投递事实层（#1167 P4 / ADR-0036 D6）：每行 = 一次「通知 × 通道」投递。

    状态词表向前兼容（D6）：``requested``（首次请求已发出）→ ``dispatched``
    （尝试进行中）→ ``accepted``（渠道接受，终态成功）/ ``retrying``（可重试
    失败，等待 SAQ 重试）→ ``failed``（永久拒绝）。未来新增 ``delivered``
    （§2.3 挂起项）不需要改契约。

    1:N：一条 NotificationLog 对应 N 行（每通道一行，unique(log, channel)）；
    与 ``NotificationLog.context.channel_delivery``（P4 前的 JSONB 记录）双写
    过渡——**本表为权威**（P4 起幂等判定读本表；无本表行的历史日志回落 JSONB）。
    """

    __tablename__ = "notification_delivery"

    id = Column(Integer, primary_key=True)
    notification_log_id = Column(
        Integer,
        ForeignKey("notification_logs.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel_id = Column(
        Integer,
        ForeignKey("notification_channels.id", ondelete="SET NULL"),
        nullable=True,
    )
    channel_type = Column(String(32), nullable=False)
    # 生命周期状态（D6 词表，小写）；outcome = 最近一次尝试的结果类（D2 大写）
    state = Column(String(16), nullable=False, default="requested")
    outcome = Column(String(32), nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    requested_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "notification_log_id", "channel_id",
            name="uq_notification_delivery_log_channel",
        ),
        Index("ix_notification_delivery_log", "notification_log_id"),
        Index("ix_notification_delivery_state", "state"),
        Index("ix_notification_delivery_outcome", "outcome"),
    )
