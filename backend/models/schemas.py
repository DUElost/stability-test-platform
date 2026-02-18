from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from backend.core.database import Base


class User(Base):
    """User model for system authentication."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(128), unique=True, nullable=False, index=True)
    hashed_password = Column(String(256), nullable=False)
    role = Column(String(32), default="user", nullable=False)
    is_active = Column(String(1), default="Y", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_login = Column(DateTime)


class HostStatus(str, PyEnum):
    OFFLINE = "OFFLINE"
    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"


class DeviceStatus(str, PyEnum):
    OFFLINE = "OFFLINE"
    ONLINE = "ONLINE"
    BUSY = "BUSY"


class TaskStatus(str, PyEnum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class RunStatus(str, PyEnum):
    QUEUED = "QUEUED"
    DISPATCHED = "DISPATCHED"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class DeploymentStatus(str, PyEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class Host(Base):
    __tablename__ = "hosts"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), unique=True, nullable=False)
    ip = Column(String(64), index=True, nullable=False)
    ssh_port = Column(Integer, default=22)
    ssh_user = Column(String(64))
    ssh_auth_type = Column(String(32), default="password")
    ssh_key_path = Column(String(256))
    status = Column(Enum(HostStatus), default=HostStatus.OFFLINE, nullable=False)
    last_heartbeat = Column(DateTime)
    extra = Column(JSON, default=dict)
    mount_status = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    devices = relationship("Device", back_populates="host")
    runs = relationship("TaskRun", back_populates="host")


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (
        Index('ix_dev_host_status', 'host_id', 'status'),
    )

    id = Column(Integer, primary_key=True)
    serial = Column(String(128), unique=True, index=True, nullable=False)
    host_id = Column(Integer, ForeignKey("hosts.id"))
    model = Column(String(128))
    status = Column(Enum(DeviceStatus), default=DeviceStatus.OFFLINE, nullable=False)
    lock_run_id = Column(Integer)
    lock_expires_at = Column(DateTime)
    last_seen = Column(DateTime)
    tags = Column(JSON, default=list)
    extra = Column(JSON, default=dict)

    # ADB 连接状态
    adb_state = Column(String(32))
    adb_connected = Column(Boolean, default=False)

    # 硬件信息
    battery_level = Column(Integer)
    battery_temp = Column(Integer)
    temperature = Column(Integer)
    wifi_rssi = Column(Integer)
    wifi_ssid = Column(String(128))
    network_latency = Column(Float)  # 网络延迟 (ms, ping 223.5.5.5 / 8.8.8.8)

    # 系统资源
    cpu_usage = Column(Float)
    mem_total = Column(BigInteger)
    mem_used = Column(BigInteger)
    disk_total = Column(BigInteger)
    disk_used = Column(BigInteger)

    # 硬件信息更新时间
    hardware_updated_at = Column(DateTime)

    host = relationship("Host", back_populates="devices")
    runs = relationship("TaskRun", back_populates="device")


class DeviceMetricSnapshot(Base):
    """Historical device metrics — one row per heartbeat per device."""
    __tablename__ = "device_metric_snapshots"
    __table_args__ = (
        Index('ix_dms_device_ts', 'device_id', 'timestamp'),
    )

    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    battery_level = Column(Integer)
    temperature = Column(Integer)
    network_latency = Column(Float)
    cpu_usage = Column(Float)
    mem_used = Column(BigInteger)


class TaskTemplate(Base):
    __tablename__ = "task_templates"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), unique=True, nullable=False)
    type = Column(String(32), nullable=False)
    description = Column(String(256))
    default_params = Column(JSON, default=dict)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    tasks = relationship("Task", back_populates="template")


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index('ix_task_status', 'status'),
    )

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    type = Column(String(32), nullable=False)
    template_id = Column(Integer, ForeignKey("task_templates.id"))
    tool_id = Column(Integer, ForeignKey("tools.id"), nullable=True)
    params = Column(JSON, default=dict)
    tool_snapshot = Column(JSON, nullable=True)
    target_device_id = Column(Integer, ForeignKey("devices.id"))
    status = Column(Enum(TaskStatus), default=TaskStatus.PENDING, nullable=False)
    priority = Column(Integer, default=0)

    # 分布式任务支持
    group_id = Column(String(32), index=True)  # 任务组ID，关联的 TaskRun 共享
    is_distributed = Column(Boolean, default=False)  # 是否为分布式任务

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    template = relationship("TaskTemplate", back_populates="tasks")
    tool = relationship("Tool")
    runs = relationship("TaskRun", back_populates="task")


class TaskRun(Base):
    __tablename__ = "task_runs"
    __table_args__ = (
        Index('ix_tr_host_status', 'host_id', 'status'),
        Index('ix_tr_task_id', 'task_id'),
        Index('ix_tr_status', 'status'),
    )

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    host_id = Column(Integer, ForeignKey("hosts.id"), nullable=False)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False)
    status = Column(Enum(RunStatus), default=RunStatus.QUEUED, nullable=False)

    # 分布式任务支持
    group_id = Column(String(32), index=True)  # 与 Task.group_id 关联

    # 进度信息
    progress = Column(Integer, default=0)  # 进度百分比
    progress_message = Column(String(256))  # 进度描述，如 "设备配置中"、"风险扫描中"

    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    last_heartbeat_at = Column(DateTime)
    exit_code = Column(Integer)
    error_code = Column(String(64))
    error_message = Column(Text)
    log_summary = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Post-completion pipeline results
    report_json = Column(JSON, nullable=True)
    jira_draft_json = Column(JSON, nullable=True)
    post_processed_at = Column(DateTime, nullable=True)

    task = relationship("Task", back_populates="runs")
    host = relationship("Host", back_populates="runs")
    device = relationship("Device", back_populates="runs")
    artifacts = relationship("LogArtifact", back_populates="run")


class LogArtifact(Base):
    __tablename__ = "log_artifacts"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("task_runs.id"), nullable=False, index=True)
    storage_uri = Column(String(512), nullable=False)
    size_bytes = Column(BigInteger)
    checksum = Column(String(128))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    run = relationship("TaskRun", back_populates="artifacts")


class Deployment(Base):
    __tablename__ = "deployments"

    id = Column(Integer, primary_key=True)
    host_id = Column(Integer, ForeignKey("hosts.id"), nullable=False, index=True)
    status = Column(Enum(DeploymentStatus), default=DeploymentStatus.PENDING, nullable=False)
    install_path = Column(String(256), default="/opt/stability-test-agent")
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at = Column(DateTime)
    logs = Column(Text)
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    host = relationship("Host")


# ==================== 工具管理模块 ====================

class ToolCategory(Base):
    """测试类型分类（Monkey、GPU、DDR、MTBF）"""
    __tablename__ = "tool_categories"

    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False, unique=True)  # 如 "Monkey", "GPU", "DDR"
    description = Column(String(256))
    icon = Column(String(32))  # 图标名
    order = Column(Integer, default=0)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    tools = relationship("Tool", back_populates="category")


class Tool(Base):
    """工具配置"""
    __tablename__ = "tools"

    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, ForeignKey("tool_categories.id"), nullable=False, index=True)

    # 基本信息
    name = Column(String(128), nullable=False)  # 如 "MTK平台 Monkey 测试"
    description = Column(String(256))

    # 脚本配置
    script_path = Column(String(512), nullable=False)  # Agent 端脚本路径
    script_class = Column(String(128))  # Python 类名，如 "MonkeyTest"
    script_type = Column(String(16), default="python")

    # 参数模板
    default_params = Column(JSON, default=dict)  # 默认参数
    param_schema = Column(JSON, default=dict)  # 参数 Schema（用于前端表单）

    # 运行配置
    timeout = Column(Integer, default=3600)
    need_device = Column(Boolean, default=True)

    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    category = relationship("ToolCategory", back_populates="tools")


# ==================== 工作流模块 ====================


class WorkflowStatus(PyEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class StepStatus(PyEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class Workflow(Base):
    __tablename__ = "workflows"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    description = Column(Text)
    status = Column(Enum(WorkflowStatus), default=WorkflowStatus.DRAFT, nullable=False)
    is_template = Column(Boolean, default=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    steps = relationship(
        "WorkflowStep",
        back_populates="workflow",
        order_by="WorkflowStep.order",
        cascade="all, delete-orphan",
    )


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (
        Index('ix_ws_task_run_id', 'task_run_id'),
    )

    id = Column(Integer, primary_key=True)
    workflow_id = Column(Integer, ForeignKey("workflows.id"), nullable=False)
    order = Column(Integer, nullable=False)
    name = Column(String(128), nullable=False)
    tool_id = Column(Integer, ForeignKey("tools.id"), nullable=True)
    task_type = Column(String(64))
    params = Column(JSON, default=dict)
    target_device_id = Column(Integer, ForeignKey("devices.id"), nullable=True)
    status = Column(Enum(StepStatus), default=StepStatus.PENDING, nullable=False)
    task_run_id = Column(Integer, ForeignKey("task_runs.id"), nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    workflow = relationship("Workflow", back_populates="steps")


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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    rules = relationship("AlertRule", back_populates="channel")


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    event_type = Column(Enum(EventType), nullable=False)
    channel_id = Column(Integer, ForeignKey("notification_channels.id"), nullable=False)
    filters = Column(JSON, default=dict)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    channel = relationship("NotificationChannel", back_populates="rules")


# ==================== 审计日志 ====================


class AuditLog(Base):
    """Audit log for tracking mutation operations."""
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index('ix_audit_user_ts', 'user_id', 'timestamp'),
        Index('ix_audit_resource', 'resource_type', 'resource_id'),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    username = Column(String(128))
    action = Column(String(64), nullable=False)  # create, update, delete, start, cancel, etc.
    resource_type = Column(String(64), nullable=False)  # task, workflow, tool, notification, etc.
    resource_id = Column(Integer)
    details = Column(JSON, default=dict)
    ip_address = Column(String(64))
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)


# ==================== 定时任务 ====================


class TaskSchedule(Base):
    """Cron-based task scheduling."""
    __tablename__ = "task_schedules"
    __table_args__ = (
        Index('ix_sched_enabled_next', 'enabled', 'next_run_at'),
    )

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    cron_expression = Column(String(128), nullable=False)  # e.g. "0 2 * * *"
    task_template_id = Column(Integer, ForeignKey("task_templates.id"), nullable=True)
    tool_id = Column(Integer, ForeignKey("tools.id"), nullable=True)
    task_type = Column(String(32), nullable=False)
    params = Column(JSON, default=dict)
    target_device_id = Column(Integer, ForeignKey("devices.id"), nullable=True)
    enabled = Column(Boolean, default=True)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
