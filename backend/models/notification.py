from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Integer, JSON, String
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
