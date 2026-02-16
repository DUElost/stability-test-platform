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
    ssh_auth_type: Optional[str] = None
    # Note: ssh_key_path is intentionally excluded for security
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
    tool_id: Optional[int] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    tool_snapshot: Optional[Dict[str, Any]] = None
    target_device_id: Optional[int] = None
    # legacy compatibility for older UI/agents
    device_serial: Optional[str] = None
    priority: int = 0

    # 分布式任务支持
    is_distributed: bool = False  # 是否为分布式任务
    device_ids: Optional[List[int]] = None  # 多个设备ID（分布式任务用）


class TaskOut(ORMBaseModel):
    id: int
    name: str
    type: str
    template_id: Optional[int] = None
    tool_id: Optional[int] = None
    params: Dict[str, Any] = {}
    tool_snapshot: Optional[Dict[str, Any]] = None
    target_device_id: Optional[int] = None
    status: str
    priority: int

    # 分布式任务支持
    group_id: Optional[str] = None
    is_distributed: bool = False
    runs_count: Optional[int] = None  # 关联的 TaskRun 数量

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

    # 分布式任务支持
    group_id: Optional[str] = None

    # 进度信息
    progress: int = 0
    progress_message: Optional[str] = None

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
    tool_id: Optional[int] = None
    tool_snapshot: Optional[Dict[str, Any]] = None


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
    # 新增：进度描述信息
    progress_message: Optional[str] = None


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


# Deployment API
class DeploymentCreate(BaseModel):
    install_path: str = "/opt/stability-test-agent"


class DeploymentOut(ORMBaseModel):
    id: int
    host_id: int
    status: str
    install_path: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    logs: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime


class DeploymentStatusOut(BaseModel):
    deployment_id: int
    host_id: int
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    error_message: Optional[str] = None


# ==================== 工具管理模块 ====================

class ToolCategoryCreate(BaseModel):
    name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    order: int = 0
    enabled: bool = True


class ToolCategoryOut(ORMBaseModel):
    id: int
    name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    order: int
    enabled: bool
    tools_count: Optional[int] = None  # 关联工具数量


class ToolCreate(BaseModel):
    category_id: int
    name: str
    description: Optional[str] = None
    script_path: str
    script_class: Optional[str] = None
    script_type: str = "python"
    default_params: Dict[str, Any] = Field(default_factory=dict)
    param_schema: Dict[str, Any] = Field(default_factory=dict)
    timeout: int = 3600
    need_device: bool = True
    enabled: bool = True


class ToolOut(ORMBaseModel):
    id: int
    category_id: int
    category_name: Optional[str] = None
    name: str
    description: Optional[str] = None
    script_path: str
    script_class: Optional[str] = None
    script_type: str
    default_params: Dict[str, Any]
    param_schema: Dict[str, Any]
    timeout: int
    need_device: bool
    enabled: bool
    created_at: datetime
    updated_at: Optional[datetime] = None


class ToolRunCreate(BaseModel):
    tool_id: int
    device_id: Optional[int] = None
    device_serial: Optional[str] = None
    host_id: Optional[int] = None
    params: Dict[str, Any] = Field(default_factory=dict)


class ToolRunOut(ORMBaseModel):
    id: int
    tool_id: int
    tool_name: Optional[str] = None
    task_run_id: Optional[int] = None
    host_id: Optional[int] = None
    device_id: Optional[int] = None
    device_serial: Optional[str] = None
    status: str
    params: Dict[str, Any]
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    exit_code: Optional[int] = None
    error_message: Optional[str] = None
    log_summary: Optional[str] = None
    created_at: datetime
