from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from backend.api.schemas.base import ORMBaseModel


class DeviceCreate(BaseModel):
    serial: str
    model: Optional[str] = None
    host_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)


class DeviceOut(ORMBaseModel):
    id: int
    serial: str
    model: Optional[str] = None
    host_id: Optional[str] = None
    status: str
    last_seen: Optional[datetime] = None
    tags: List[str] = Field(default_factory=list)
    extra: Dict[str, Any] = Field(default_factory=dict)
    adb_state: Optional[str] = None
    adb_connected: Optional[bool] = None
    battery_level: Optional[int] = None
    battery_temp: Optional[int] = None
    temperature: Optional[int] = None
    wifi_rssi: Optional[int] = None
    wifi_ssid: Optional[str] = None
    network_latency: Optional[float] = None
    build_display_id: Optional[str] = None
    cpu_usage: Optional[float] = None
    mem_total: Optional[int] = None
    mem_used: Optional[int] = None
    disk_total: Optional[int] = None
    disk_used: Optional[int] = None

    @field_validator('tags', mode='before')
    @classmethod
    def _coerce_tags(cls, v):
        if v is None:
            return []
        if isinstance(v, dict):
            return []
        return v

    @field_validator('extra', mode='before')
    @classmethod
    def _coerce_extra(cls, v):
        return v or {}


class DeviceLiteOut(ORMBaseModel):
    id: int
    serial: str
    model: Optional[str] = None
    host_id: Optional[str] = None
    status: str
