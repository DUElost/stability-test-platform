from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import model_validator, BaseModel, Field, field_validator

from backend.agent.contracts.device_return import DISARM_RESULTS, FAMILY_STEPS
from backend.api.schemas.base import ORMBaseModel
from backend.core.device_lifecycle import is_retire_suggested, is_stale
from backend.core.device_serial import is_placeholder_serial


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


class BulkSkipOobeIn(BaseModel):
    """设备页批量快捷跳过 OOBE（复用脚本库 oobe_skip）。"""

    device_ids: List[int]


class BulkSkipOobeDeviceResult(BaseModel):
    device_id: int
    serial: str
    status: str  # ok | failed | skipped
    error: Optional[str] = None


class BulkSkipOobeOut(BaseModel):
    script_version: str
    ok: int = 0
    failed: int = 0
    skipped: int = 0
    results: List[BulkSkipOobeDeviceResult] = Field(default_factory=list)


class DeviceLeaseReleaseIn(BaseModel):
    """#3646：管理员紧急释放一条终态 job 租约。原因写入审计，不得为空。"""

    reason: str = Field(min_length=1)

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("reason must not be blank")
        return stripped


class DeviceLeaseReleaseOut(BaseModel):
    lease_id: int
    device_id: int
    job_id: int
    status: str


class DeviceRetireIn(BaseModel):
    """ADR-0057 D2：设备退役请求体——原因必填（审计 who/when/reason 的 reason）。"""

    retire_reason: str = Field(min_length=1)


class DeviceUnretireIn(BaseModel):
    """ADR-0057 D2：解除退役请求体——原因同样必填（与 retire 审计对称）。"""

    retire_reason: str = Field(min_length=1)


class DeviceRebindIn(BaseModel):
    """#3892：设备改绑请求体——目标主机 + 原因必填（审计 from/to/reason）。"""

    host_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)

    @field_validator("host_id", "reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


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



class DeviceReturnDebtOut(ORMBaseModel):
    """ADR-0061 D5.9 / #3723 U6：设备未关闭欠账条目（只读，不含 evidence）。"""

    id: int
    family: str
    source_kind: str
    source_job_id: Optional[int] = None
    cause: str
    created_at: datetime


class DeviceReturnOverrideIn(BaseModel):
    """ADR-0061 D5.7：人工放行——原因类别 + 证据引用必填。"""

    families: Optional[List[str]] = None  # None / empty = all families
    reason_category: str = Field(min_length=1)
    evidence_ref: str = Field(min_length=1)

    @field_validator("reason_category", "evidence_ref")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class DeviceReturnOverrideOut(BaseModel):
    device_id: int
    families: List[str]
    closed_count: int
    debt_lease_released: bool
    open_debt_count: int


class AgentReturnDischargeIn(BaseModel):
    """ADR-0061 D5.5：Agent 清偿回报（携带欠账租约令牌）。"""

    fencing_token: str = Field(min_length=1)
    family_results: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    @field_validator("fencing_token")
    @classmethod
    def _token_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("fencing_token must not be blank")
        return stripped


class AgentReturnDischargeOut(BaseModel):
    device_id: int
    discharged_families: List[str] = Field(default_factory=list)
    debt_lease_released: bool = False
    open_debt_count: int = 0
    fencing_token: Optional[str] = None


class AgentInitReturnCheckIn(AgentReturnDischargeIn):
    """Job-token protected init evidence; separate from terminal disarm_results."""

    @field_validator("family_results")
    @classmethod
    def _known_results(cls, value: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        if not value or any(f not in FAMILY_STEPS or e.get("result") not in DISARM_RESULTS
                            for f, e in value.items()):
            raise ValueError("family_results must contain known families and formal results")
        return value


class AgentInitReturnCheckOut(BaseModel):
    job_id: int
    accepted_families: List[str] = Field(default_factory=list)
    open_debt_count: int = 0


class DeviceOut(ORMBaseModel):
    id: int
    serial: str
    model: Optional[str] = None
    product: Optional[str] = None
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
    # ADR-0061 D1.3 / #3723 U3：按专项归还状态（可空=未初始化）。
    return_state: Optional[Dict[str, Any]] = None
    # ADR-0063 D7 / #3918：vbus 槽位 USB 在位（现算派生，不落库）。只有主机上报了槽位映射、
    # 且本设备在映射里时才有值：{slot, vid_pid, state, reported_at, host_online}。
    usb_presence: Optional[Dict[str, Any]] = None
    # #3904 / #4185：当前 ACTIVE 租约只读投影（不改 Device.status；列表/详情批量填充，零 N+1）。
    active_lease_type: Optional[str] = None
    active_lease_reason: Optional[str] = None

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
    product: Optional[str] = None
    host_id: Optional[str] = None
    status: str


DEVICE_LIST_MAX_TAGS = 16
DEVICE_LIST_MAX_TAG_BYTES = 64
DEVICE_LIST_MAX_TAGS_TOTAL_BYTES = 512
DEVICE_LIST_MAX_TEXT_BYTES = 256
DEVICE_LIST_MAX_RETURN_FAMILIES = 16
DEVICE_LIST_RETURN_VALUE_BYTES = 64


def _truncate_utf8(value: str, max_bytes: int) -> tuple[str, bool]:
    """按 UTF-8 字节预算安全截断字符串，保证不切碎多字节字符。"""
    raw = value.encode("utf-8")
    if len(raw) <= max_bytes:
        return value, False
    ellipsis = "…"
    ellipsis_bytes = len(ellipsis.encode("utf-8"))
    if max_bytes <= ellipsis_bytes:
        return raw[:max_bytes].decode("utf-8", errors="ignore"), True
    trimmed = raw[: max_bytes - ellipsis_bytes].decode("utf-8", errors="ignore")
    return f"{trimmed}{ellipsis}", True


def _bound_list_tags(raw_tags: Any) -> tuple[List[str], bool]:
    """将列表投影中的标签数组收敛在固定条数与 UTF-8 字节预算内。"""
    if not isinstance(raw_tags, list):
        return [], False
    bounded: List[str] = []
    truncated = False
    total_bytes = 0
    for item in raw_tags:
        if len(bounded) >= DEVICE_LIST_MAX_TAGS:
            truncated = True
            break
        text = str(item)
        clipped, was_clipped = _truncate_utf8(text, DEVICE_LIST_MAX_TAG_BYTES)
        item_bytes = len(clipped.encode("utf-8"))
        if total_bytes + item_bytes > DEVICE_LIST_MAX_TAGS_TOTAL_BYTES:
            remaining = DEVICE_LIST_MAX_TAGS_TOTAL_BYTES - total_bytes
            if remaining >= 8:
                clipped, _ = _truncate_utf8(clipped, remaining)
                bounded.append(clipped)
            truncated = True
            break
        if was_clipped:
            truncated = True
        bounded.append(clipped)
        total_bytes += item_bytes
    return bounded, truncated


def _bound_list_return_state(raw_state: Any) -> Optional[Dict[str, Any]]:
    """列表投影仅保留每个专项的最小三键摘要，防异常脏数据撑爆单页载荷。"""
    if not isinstance(raw_state, dict):
        return None
    out: Dict[str, Any] = {}
    for family, entry in list(raw_state.items())[:DEVICE_LIST_MAX_RETURN_FAMILIES]:
        fam_key, _ = _truncate_utf8(str(family), DEVICE_LIST_RETURN_VALUE_BYTES)
        if not isinstance(entry, dict):
            continue
        compact: Dict[str, Any] = {}
        for field in ("state", "source", "updated_at"):
            val = entry.get(field)
            if val is None:
                continue
            if isinstance(val, str):
                compact[field], _ = _truncate_utf8(val, DEVICE_LIST_RETURN_VALUE_BYTES)
            else:
                compact[field] = val
        out[fam_key] = compact
    return out


class DeviceListItemOut(ORMBaseModel):
    """#496 / #4117 B2-1：物理设备有界列表 DTO（``projection=list``）。

    刻意省略无界 ``extra`` 字典，并对 ``tags``、长文本字段与 ``return_state`` 施加严格
    UTF-8 字节上界，确保单页（50 条）即使在最坏合法长标签场景下仍稳定低于 100 KiB；
    完整未截断字段由单机详情 ``GET /api/v1/devices/{id}`` 按需提供。
    """

    id: int
    serial: str
    serial_suspect: bool = False
    model: Optional[str] = None
    product: Optional[str] = None
    platform: Optional[str] = None
    host_id: Optional[str] = None
    host_name: Optional[str] = None
    project_key: Optional[str] = None
    attribution_source: Optional[str] = None
    status: str
    last_seen: Optional[datetime] = None
    tags: List[str] = Field(default_factory=list)
    tags_truncated: bool = False
    adb_state: Optional[str] = None
    adb_connected: Optional[bool] = None
    battery_level: Optional[int] = None
    battery_temp: Optional[int] = None
    temperature: Optional[int] = None
    wifi_rssi: Optional[int] = None
    wifi_ssid: Optional[str] = None
    network_latency: Optional[float] = None
    build_display_id: Optional[str] = None
    disk_total: Optional[int] = None
    disk_used: Optional[int] = None
    retired_at: Optional[datetime] = None
    retired_by: Optional[str] = None
    retire_reason: Optional[str] = None
    is_stale: bool = False
    retire_suggested: bool = False
    return_state: Optional[Dict[str, Any]] = None
    usb_presence: Optional[Dict[str, Any]] = None
    # #3904 / #4185：当前 ACTIVE 租约只读投影（与 DeviceOut 同源；列表批量填充）。
    active_lease_type: Optional[str] = None
    active_lease_reason: Optional[str] = None

    @model_validator(mode="after")
    def _mark_serial_suspect(self):
        self.serial_suspect = is_placeholder_serial(self.serial)
        return self

    @model_validator(mode="after")
    def _mark_lifecycle_flags(self):
        self.is_stale = is_stale(self.status, self.last_seen)
        self.retire_suggested = is_retire_suggested(self.status, self.last_seen)
        return self

    @model_validator(mode="after")
    def _bound_list_payload(self):
        bounded_tags, was_truncated = _bound_list_tags(self.tags)
        self.tags = bounded_tags
        self.tags_truncated = bool(self.tags_truncated or was_truncated)
        for attr in (
            "model",
            "product",
            "host_name",
            "wifi_ssid",
            "build_display_id",
            "retired_by",
            "retire_reason",
            "active_lease_reason",
        ):
            val = getattr(self, attr, None)
            if isinstance(val, str):
                clipped, _ = _truncate_utf8(val, DEVICE_LIST_MAX_TEXT_BYTES)
                setattr(self, attr, clipped)
        if self.return_state is not None:
            self.return_state = _bound_list_return_state(self.return_state)
        return self

    @field_validator("tags", mode="before")
    @classmethod
    def _coerce_tags(cls, v):
        if v is None or isinstance(v, dict):
            return []
        return v


class DeviceFacetStatusCountsOut(BaseModel):
    """#496 / #4117 B2-1：作用域 B 下的设备状态计数卡。"""

    all: int = 0
    ONLINE: int = 0
    BUSY: int = 0
    OFFLINE: int = 0
    ERROR: int = 0
    need_attention: int = 0
    tagged: int = 0


class DeviceHostFacetOptionOut(BaseModel):
    """主机下拉候选项（无主机桶使用 ``value='unassigned'``）。"""

    value: str
    label: str


class DeviceFacetOptionsOut(BaseModel):
    """#496 / #4117 B2-1：作用域 H/D 下的下拉候选项（首版不含未消费的 ``tags``）。"""

    hosts: List[DeviceHostFacetOptionOut] = Field(default_factory=list)
    models: List[str] = Field(default_factory=list)
    products: List[str] = Field(default_factory=list)
    versions: List[str] = Field(default_factory=list)


class DeviceFacetsOut(BaseModel):
    """``GET /api/v1/devices/facets`` 响应模型。"""

    status_counts: DeviceFacetStatusCountsOut
    options: DeviceFacetOptionsOut


class DeviceSelectionPreviewIn(BaseModel):
    """``POST /api/v1/devices/selection-preview`` 请求体：按筛选条件冻结跨页批量操作集合。"""

    action: Literal[
        "tags",
        "assign_project",
        "swipe_trail",
        "skip_oobe",
        "retire",
        "unretire",
        "copy",
        "export",
    ]
    q: Optional[str] = Field(default=None, max_length=256)
    status: Optional[str] = Field(default=None, max_length=64)
    host_id: Optional[str] = Field(default=None, max_length=64)
    model: Optional[str] = Field(default=None, max_length=128)
    product: Optional[str] = Field(default=None, max_length=128)
    version: Optional[str] = Field(default=None, max_length=256)
    tags: Optional[str] = Field(default=None, max_length=512)
    has_tags: Optional[bool] = None
    project_key: Optional[str] = Field(default=None, max_length=128)
    unassigned: bool = False
    include_stale: bool = False
    include_retired: bool = False


class DeviceSelectionPreviewOut(BaseModel):
    """``POST /api/v1/devices/selection-preview`` 响应模型。"""

    action: str
    total_matched: int
    max_allowed: Optional[int] = None
    exceeds_limit: bool = False
    device_ids: List[int] = Field(default_factory=list)
    status_counts: DeviceFacetStatusCountsOut
    affected_models: List[str] = Field(default_factory=list)
    devices_without_model_serials: List[str] = Field(default_factory=list)
    retired_count: int = 0
    active_count: int = 0
    stale_count: int = 0
