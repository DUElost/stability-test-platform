from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

from backend.api.schemas.base import ORMBaseModel


class NotificationChannelCreate(BaseModel):
    name: str
    type: Literal["WEBHOOK", "EMAIL", "DINGTALK"]
    config: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class NotificationChannelUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[Literal["WEBHOOK", "EMAIL", "DINGTALK"]] = None
    config: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None


class NotificationChannelOut(ORMBaseModel):
    id: int
    name: str
    type: str
    config: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool
    created_at: datetime


class AlertRuleCreate(BaseModel):
    name: str
    event_type: Literal["RUN_COMPLETED", "RUN_FAILED", "RISK_HIGH", "DEVICE_OFFLINE"]
    channel_id: int
    filters: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class AlertRuleUpdate(BaseModel):
    name: Optional[str] = None
    event_type: Optional[Literal["RUN_COMPLETED", "RUN_FAILED", "RISK_HIGH", "DEVICE_OFFLINE"]] = None
    channel_id: Optional[int] = None
    filters: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None


class AlertRuleOut(ORMBaseModel):
    id: int
    name: str
    event_type: str
    channel_id: int
    channel_name: Optional[str] = None
    filters: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool
    created_at: datetime
