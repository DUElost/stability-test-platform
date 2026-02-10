from datetime import datetime
from typing import Any, Dict, Optional, Literal, List

from pydantic import BaseModel, Field

try:
    from pydantic import ConfigDict

    _HAS_CONFIG_DICT = True
except Exception:
    ConfigDict = None
    _HAS_CONFIG_DICT = False


class ORMBaseModel(BaseModel):
    if _HAS_CONFIG_DICT:
        model_config = ConfigDict(from_attributes=True)
    else:
        class Config:
            orm_mode = True


class HostCreate(BaseModel):
    name: str
    ip: str
    ssh_port: int = 22
    ssh_user: Optional[str] = None
    ssh_auth_type: str = "password"
    ssh_key_path: Optional[str] = None


class HostOut(ORMBaseModel):
    id: int
    name: str
    ip: str
    ssh_port: int
    ssh_user: Optional[str] = None
    status: str
    last_heartbeat: Optional[datetime] = None
    extra: Dict[str, Any] = {}
    mount_status: Dict[str, Any] = {}


class HeartbeatIn(BaseModel):
    host_id: int
    status: Literal["ONLINE", "OFFLINE", "DEGRADED"]
    mount_status: Dict[str, Any] = Field(default_factory=dict)
    extra: Dict[str, Any] = Field(default_factory=dict)
    # 主机信息（可选，用于自动创建主机）
    host: Optional[Dict[str, Any]] = None
    # 设备数组（可选，用于设备上报）
    devices: List[Dict[str, Any]] = Field(default_factory=list)


class TaskCreate(BaseModel):
    name: str
    type: str
    template_id: Optional[int] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    target_device_id: Optional[int] = None
    # legacy compatibility for older UI/agents
    device_serial: Optional[str] = None
    priority: int = 0


class TaskOut(ORMBaseModel):
    id: int
    name: str
    type: str
    template_id: Optional[int] = None
    params: Dict[str, Any] = {}
    target_device_id: Optional[int] = None
    status: str
    priority: int
    created_at: datetime


class TaskTemplateOut(BaseModel):
    type: str
    name: str
    description: str
    default_params: Dict[str, Any] = Field(default_factory=dict)
    script_paths: Dict[str, str] = Field(default_factory=dict)


class TaskDispatch(BaseModel):
    host_id: int
    device_id: int


class LogArtifactOut(ORMBaseModel):
    id: int
    run_id: int
    storage_uri: str
    size_bytes: Optional[int] = None
    checksum: Optional[str] = None
    created_at: datetime


class RunOut(ORMBaseModel):
    id: int
    task_id: int
    host_id: int
    device_id: int
    status: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    exit_code: Optional[int] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    log_summary: Optional[str] = None
    artifacts: List["LogArtifactOut"] = Field(default_factory=list)
    risk_summary: Optional[Dict[str, Any]] = None


class HostLiteOut(ORMBaseModel):
    id: int
    name: str
    ip: str
    status: str


class DeviceLiteOut(ORMBaseModel):
    id: int
    serial: str
    model: Optional[str] = None
    host_id: Optional[int] = None
    status: str


class RiskAlertOut(BaseModel):
    code: str
    severity: Literal["HIGH", "MEDIUM", "LOW"]
    message: str
    metric: Optional[str] = None
    value: Optional[int] = None
    threshold: Optional[int] = None


class RunReportOut(BaseModel):
    generated_at: datetime
    run: RunOut
    task: TaskOut
    host: Optional[HostLiteOut] = None
    device: Optional[DeviceLiteOut] = None
    summary_metrics: Dict[str, Any] = Field(default_factory=dict)
    risk_summary: Optional[Dict[str, Any]] = None
    alerts: List[RiskAlertOut] = Field(default_factory=list)


class JiraDraftOut(BaseModel):
    run_id: int
    task_id: int
    project_key: str
    issue_type: str = "Bug"
    priority: Literal["Critical", "Major", "Minor"]
    component: Optional[str] = None
    fix_version: Optional[str] = None
    assignee: Optional[str] = None
    summary: str
    description: str
    labels: List[str] = Field(default_factory=list)
    environment: Dict[str, Any] = Field(default_factory=dict)
    custom_fields: Dict[str, Any] = Field(default_factory=dict)
    extra: Dict[str, Any] = Field(default_factory=dict)


class RunAgentOut(BaseModel):
    id: int
    task_id: int
    host_id: int
    device_id: int
    device_serial: Optional[str] = None
    task_type: str
    task_params: Dict[str, Any] = Field(default_factory=dict)


class RunUpdate(BaseModel):
    status: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    exit_code: Optional[int] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    log_summary: Optional[str] = None
    # 新增：日志行列表，用于实时推送
    log_lines: Optional[List[str]] = None
    # 新增：日志进度百分比
    progress: Optional[int] = None


class LogArtifactIn(BaseModel):
    storage_uri: str
    size_bytes: Optional[int] = None
    checksum: Optional[str] = None


class RunCompleteIn(BaseModel):
    update: RunUpdate
    artifact: Optional[LogArtifactIn] = None


class DeviceCreate(BaseModel):
    serial: str
    model: Optional[str] = None
    host_id: Optional[int] = None
    tags: List[str] = Field(default_factory=list)


class DeviceOut(ORMBaseModel):
    id: int
    serial: str
    model: Optional[str] = None
    host_id: Optional[int] = None
    status: str
    last_seen: Optional[datetime] = None
    tags: List[str] = Field(default_factory=list)
    extra: Dict[str, Any] = Field(default_factory=dict)

    # ADB 连接状态
    adb_state: Optional[str] = None
    adb_connected: Optional[bool] = None

    # 硬件信息
    battery_level: Optional[int] = None
    battery_temp: Optional[int] = None
    temperature: Optional[int] = None
    wifi_rssi: Optional[int] = None
    wifi_ssid: Optional[str] = None
    network_latency: Optional[float] = None  # 网络延迟 (ms)

    # 系统资源
    cpu_usage: Optional[float] = None
    mem_total: Optional[int] = None
    mem_used: Optional[int] = None
    disk_total: Optional[int] = None
    disk_used: Optional[int] = None


# Agent日志查询
class AgentLogQuery(BaseModel):
    host_id: int
    log_path: str = "/tmp/agent.log"  # 默认日志路径
    lines: int = 100  # 默认读取最后100行


class AgentLogOut(BaseModel):
    host_id: int
    log_path: str
    content: str
    lines_read: int
    error: Optional[str] = None
