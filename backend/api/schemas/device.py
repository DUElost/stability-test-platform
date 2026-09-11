from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import model_validator, BaseModel, Field, field_validator

from backend.api.schemas.base import ORMBaseModel


class DeviceCreate(BaseModel):
    serial: str
    model: Optional[str] = None
    host_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)


class BulkProjectAssignIn(BaseModel):
    """ADR-0029 P2 — 设备批量归入项目请求体。"""

    project_key: str
    device_ids: List[int]


class DeviceOut(ORMBaseModel):
    id: int
    serial: str
    model: Optional[str] = None
    platform: Optional[str] = None  # #73: MTK / UNISOC / QCOM / UNKNOWN
    host_id: Optional[str] = None
    project_key: Optional[str] = None  # ADR-0029：归属项目（F2 口径，不暴露 project_id）
    # ADR-0029 v2.5 D10：归属来源两态——mapped=型号有活跃成员行；
    # unmapped=无型号设备或型号未映射。无 pinned 例外（M3 删列）。
    attribution_source: Optional[str] = None
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
    # #1356：serial 为占位值（跨 host 可重复→归属漂移）——读侧标记，供前端/运维识别
    serial_suspect: bool = False
    cpu_usage: Optional[float] = None
    mem_total: Optional[int] = None
    mem_used: Optional[int] = None
    disk_total: Optional[int] = None
    disk_used: Optional[int] = None

    @model_validator(mode='after')
    def _mark_serial_suspect(self):
        # #1356：占位 serial 标记（不落库，读时派生）
        from backend.core.device_serial import is_placeholder_serial
        self.serial_suspect = is_placeholder_serial(self.serial)
        return self

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
