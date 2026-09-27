from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import model_validator, BaseModel, Field, field_validator

from backend.api.schemas.base import ORMBaseModel
from backend.core.device_lifecycle import is_retire_suggested, is_stale


class DeviceCreate(BaseModel):
    serial: str
    model: Optional[str] = None
    host_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)


class BulkProjectAssignIn(BaseModel):
    """ADR-0029 P2 — 设备批量归入项目请求体。"""

    project_key: str
    device_ids: List[int]


class BulkSwipeTrailIn(BaseModel):
    """设备页批量开关滑动留痕（show_touches + pointer_location）。"""

    device_ids: List[int]
    enabled: bool


class BulkSwipeTrailDeviceResult(BaseModel):
    device_id: int
    serial: str
    status: str  # ok | failed | skipped
    error: Optional[str] = None


class BulkSwipeTrailOut(BaseModel):
    enabled: bool
    ok: int = 0
    failed: int = 0
    skipped: int = 0
    results: List[BulkSwipeTrailDeviceResult] = Field(default_factory=list)


class DeviceRetireIn(BaseModel):
    """ADR-0057 D2：设备退役请求体——原因必填（审计 who/when/reason 的 reason）。"""

    retire_reason: str = Field(min_length=1)


class DeviceUnretireIn(BaseModel):
    """ADR-0057 D2：解除退役请求体——原因同样必填（与 retire 审计对称）。"""

    retire_reason: str = Field(min_length=1)


class DeviceRetireBatchIn(BaseModel):
    """ADR-0057 D2/E3：批量退役请求体（逐台独立事务，任一台失败不影响其他台）。"""

    device_ids: List[int]
    retire_reason: str = Field(min_length=1)


class DeviceRetireBatchResult(BaseModel):
    device_id: int
    serial: Optional[str] = None
    # retired | already_retired | conflict | not_found | failed
    status: str
    error: Optional[str] = None


class DeviceRetireBatchOut(BaseModel):
    results: List[DeviceRetireBatchResult] = Field(default_factory=list)
    retired: int = 0
    already_retired: int = 0
    conflict: int = 0
    not_found: int = 0
    failed: int = 0


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
    # ADR-0057 D1（#2962 B）：退役事实（NULL = 在役；不改写 status，与心跳正交）。
    retired_at: Optional[datetime] = None
    retired_by: Optional[str] = None
    retire_reason: Optional[str] = None
    # #2962 A/D6：陈旧度与退役建议是**现算派生**（不落库），供列表隐藏/徽标/建议。
    is_stale: bool = False
    retire_suggested: bool = False

    @model_validator(mode='after')
    def _mark_serial_suspect(self):
        # #1356：占位 serial 标记（不落库，读时派生）
        from backend.core.device_serial import is_placeholder_serial
        self.serial_suspect = is_placeholder_serial(self.serial)
        return self

    @model_validator(mode='after')
    def _mark_lifecycle_flags(self):
        # #2962 A + ADR-0057 E4：陈旧/退役建议同源派生（口径在 device_lifecycle）。
        self.is_stale = is_stale(self.status, self.last_seen)
        self.retire_suggested = is_retire_suggested(self.status, self.last_seen)
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
