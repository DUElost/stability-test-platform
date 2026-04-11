from datetime import datetime
from typing import Any, Dict, Optional, Literal, List

from pydantic import BaseModel, Field, field_validator

try:
    from pydantic import ConfigDict

    _HAS_CONFIG_DICT = True
except Exception:
    ConfigDict = None
    _HAS_CONFIG_DICT = False


def _isoformat_utc(v: datetime) -> str:
    return v.isoformat() + "Z" if v.tzinfo is None else v.isoformat()


class ORMBaseModel(BaseModel):
    if _HAS_CONFIG_DICT:
        model_config = ConfigDict(from_attributes=True, json_encoders={datetime: _isoformat_utc})
    else:
        class Config:
            orm_mode = True
            json_encoders = {datetime: _isoformat_utc}


class HostCreate(BaseModel):
    name: str
    ip: str
    ssh_port: int = 22
    ssh_user: Optional[str] = None
    ssh_auth_type: str = "password"
    ssh_key_path: Optional[str] = None


class HostOut(ORMBaseModel):
    id: str
    name: Optional[str] = None
    ip: Optional[str] = None
    ssh_port: Optional[int] = 22
    ssh_user: Optional[str] = None
    ssh_auth_type: Optional[str] = None
    # Note: ssh_key_path is intentionally excluded for security
    status: str
    last_heartbeat: Optional[datetime] = None
    extra: Dict[str, Any] = {}
    mount_status: Dict[str, Any] = {}

    @field_validator('extra', 'mount_status', mode='before')
    @classmethod
    def _coerce_none_to_dict(cls, v):
        return v or {}


class HeartbeatIn(BaseModel):
    host_id: str
    status: Literal["ONLINE", "OFFLINE", "DEGRADED"]
    mount_status: Dict[str, Any] = Field(default_factory=dict)
    extra: Dict[str, Any] = Field(default_factory=dict)
    # 主机信息（可选，用于自动创建主机）
    host: Optional[Dict[str, Any]] = None
    # 设备数组（可选，用于设备上报）
    devices: List[Dict[str, Any]] = Field(default_factory=list)

    @field_validator('host_id', mode='before')
    @classmethod
    def coerce_str(cls, v):
        return str(v)


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

    # Pipeline 定义
    pipeline_def: Optional[Dict[str, Any]] = None


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

    # Pipeline 定义
    pipeline_def: Optional[Dict[str, Any]] = None

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
    id: str
    name: Optional[str] = None
    ip: Optional[str] = None
    status: str


class DeviceLiteOut(ORMBaseModel):
    id: int
    serial: str
    model: Optional[str] = None
    host_id: Optional[str] = None
    status: str


class RiskAlertOut(BaseModel):
    code: str
    severity: Literal["HIGH", "MEDIUM", "LOW"]
    message: str
    metric: Optional[str] = None
    value: Optional[int] = None
    threshold: Optional[int] = None


class RunReportOut(ORMBaseModel):
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
    pipeline_def: Optional[Dict[str, Any]] = None


# ==================== RunStep (Pipeline 子步骤) ====================


class RunStepCreate(BaseModel):
    run_id: int
    phase: str
    step_order: int
    name: str
    action: str
    params: Dict[str, Any] = Field(default_factory=dict)


class RunStepUpdate(BaseModel):
    status: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    exit_code: Optional[int] = None
    error_message: Optional[str] = None
    log_line_count: Optional[int] = None


class RunStepOut(ORMBaseModel):
    id: int
    run_id: int
    phase: str
    step_order: int
    name: str
    action: str
    params: Dict[str, Any] = Field(default_factory=dict)
    status: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    exit_code: Optional[int] = None
    error_message: Optional[str] = None
    log_line_count: int = 0
    created_at: datetime


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
    build_display_id: Optional[str] = None  # ro.build.display.id

    # 系统资源
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


class DeploymentStatusOut(ORMBaseModel):
    deployment_id: int
    host_id: int
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    error_message: Optional[str] = None


# ==================== 工具管理模块（DEPRECATED — see tool_catalog.py） ====================
# 以下 schema 仅供旧 tools.py 路由使用，该路由已废弃且不再挂载。
# 新代码请使用 tool_catalog.py 中内联定义的 ToolCreate/ToolOut。
    created_at: datetime


# ==================== 工作流模块 ====================


class WorkflowStepCreate(BaseModel):
    name: str
    tool_id: Optional[int] = None
    task_type: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    target_device_id: Optional[int] = None


class WorkflowCreate(BaseModel):
    name: str
    description: Optional[str] = None
    steps: List[WorkflowStepCreate]


class WorkflowStepOut(ORMBaseModel):
    id: int
    workflow_id: int
    order: int
    name: str
    tool_id: Optional[int] = None
    task_type: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    target_device_id: Optional[int] = None
    status: str
    task_run_id: Optional[int] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class WorkflowOut(ORMBaseModel):
    id: int
    name: str
    description: Optional[str] = None
    status: str
    is_template: bool = False
    created_by: Optional[int] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    steps: List[WorkflowStepOut] = Field(default_factory=list)


# ==================== 通知模块 ====================


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


# ==================== 通用分页 ====================


class PaginatedResponse(BaseModel):
    """Generic paginated response wrapper."""
    items: List[Any]
    total: int
    skip: int
    limit: int


# ==================== 审计日志 ====================


class AuditLogOut(ORMBaseModel):
    id: int
    user_id: Optional[int] = None
    username: Optional[str] = None
    action: str
    resource_type: str
    resource_id: Optional[int] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    ip_address: Optional[str] = None
    timestamp: datetime


# ==================== 定时任务 ====================


class TaskScheduleCreate(BaseModel):
    if _HAS_CONFIG_DICT:
        model_config = ConfigDict(populate_by_name=True)
    else:
        class Config:
            allow_population_by_field_name = True

    name: str
    cron_expression: str = Field(alias="cron_expr")
    task_template_id: Optional[int] = None
    tool_id: Optional[int] = None
    task_type: Optional[str] = "WORKFLOW"
    params: Dict[str, Any] = Field(default_factory=dict, alias="task_params")
    target_device_id: Optional[int] = None
    workflow_definition_id: Optional[int] = None
    device_ids: List[int] = Field(default_factory=list)
    enabled: bool = True


class TaskScheduleUpdate(BaseModel):
    if _HAS_CONFIG_DICT:
        model_config = ConfigDict(populate_by_name=True)
    else:
        class Config:
            allow_population_by_field_name = True

    name: Optional[str] = None
    cron_expression: Optional[str] = Field(default=None, alias="cron_expr")
    task_template_id: Optional[int] = None
    tool_id: Optional[int] = None
    task_type: Optional[str] = None
    params: Optional[Dict[str, Any]] = Field(default=None, alias="task_params")
    target_device_id: Optional[int] = None
    workflow_definition_id: Optional[int] = None
    device_ids: Optional[List[int]] = None
    enabled: Optional[bool] = None


class TaskScheduleOut(ORMBaseModel):
    if _HAS_CONFIG_DICT:
        model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    else:
        class Config:
            orm_mode = True
            allow_population_by_field_name = True

    id: int
    name: str
    cron_expression: str = Field(alias="cron_expr")
    task_template_id: Optional[int] = None
    tool_id: Optional[int] = None
    task_type: str
    params: Dict[str, Any] = Field(default_factory=dict, alias="task_params")
    target_device_id: Optional[int] = None
    workflow_definition_id: Optional[int] = None
    device_ids: Optional[List[int]] = None
    enabled: bool
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    created_by: Optional[int] = None
    created_at: datetime


# ==================== 任务模板（DB-backed CRUD）====================


class TaskTemplateDBCreate(BaseModel):
    if _HAS_CONFIG_DICT:
        model_config = ConfigDict(populate_by_name=True)
    else:
        class Config:
            allow_population_by_field_name = True

    name: str
    type: str = Field(alias="task_type")
    description: Optional[str] = None
    default_params: Dict[str, Any] = Field(default_factory=dict, alias="params")
    enabled: bool = True


class TaskTemplateDBUpdate(BaseModel):
    if _HAS_CONFIG_DICT:
        model_config = ConfigDict(populate_by_name=True)
    else:
        class Config:
            allow_population_by_field_name = True

    name: Optional[str] = None
    type: Optional[str] = Field(default=None, alias="task_type")
    description: Optional[str] = None
    default_params: Optional[Dict[str, Any]] = Field(default=None, alias="params")
    enabled: Optional[bool] = None


class TaskTemplateDBOut(ORMBaseModel):
    if _HAS_CONFIG_DICT:
        model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    else:
        class Config:
            orm_mode = True
            allow_population_by_field_name = True

    id: int
    name: str
    type: str = Field(alias="task_type")
    description: Optional[str] = None
    default_params: Dict[str, Any] = Field(default_factory=dict, alias="params")
    enabled: bool
    created_at: datetime
