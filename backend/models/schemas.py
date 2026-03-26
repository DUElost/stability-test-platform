from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from backend.core.database import Base
from backend.models.enums import (  # noqa: F401 — canonical source; re-exported for back-compat
    DeviceStatus,
    HostStatus,
    RunStatus,
    RunStepStatus,
    TaskStatus,
)
from backend.models.user import User  # noqa: F401 — re-export for back-compat



class TaskTemplate(Base):
    __tablename__ = "task_templates"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), unique=True, nullable=False)
    type = Column(String(32), nullable=False)
    description = Column(String(256))
    default_params = Column(JSON, default=dict)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    tasks = relationship("backend.models.schemas.Task", back_populates="template")


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

    # Pipeline 定义（JSON Schema validated）
    pipeline_def = Column(JSON, nullable=True)

    template = relationship("backend.models.schemas.TaskTemplate", back_populates="tasks")
    tool = relationship("backend.models.schemas.Tool")
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
    artifacts = relationship("LogArtifact", back_populates="run")
    steps = relationship("RunStep", back_populates="run", order_by="RunStep.phase, RunStep.step_order", cascade="all, delete-orphan")


class RunStep(Base):
    """Pipeline step execution record within a TaskRun."""
    __tablename__ = "run_steps"
    __table_args__ = (
        Index('ix_rs_run_id', 'run_id'),
        Index('ix_rs_run_status', 'run_id', 'status'),
    )

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("task_runs.id", ondelete="CASCADE"), nullable=False)
    phase = Column(String(64), nullable=False)
    step_order = Column(Integer, nullable=False)
    name = Column(String(128), nullable=False)
    action = Column(String(256), nullable=False)
    params = Column(JSON, default=dict)
    status = Column(Enum(RunStepStatus), default=RunStepStatus.PENDING, nullable=False)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    exit_code = Column(Integer)
    error_message = Column(Text)
    log_line_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    run = relationship("TaskRun", back_populates="steps")


class LogArtifact(Base):
    __tablename__ = "log_artifacts"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("task_runs.id"), nullable=False, index=True)
    storage_uri = Column(String(512), nullable=False)
    size_bytes = Column(BigInteger)
    checksum = Column(String(128))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    run = relationship("TaskRun", back_populates="artifacts")


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

    tools = relationship("backend.models.schemas.Tool", back_populates="category")


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

    category = relationship("backend.models.schemas.ToolCategory", back_populates="tools")


# ==================== 通知模块（canonical: backend.models.notification） ====================
from backend.models.notification import (  # noqa: F401 — re-export for back-compat
    AlertRule,
    ChannelType,
    EventType,
    NotificationChannel,
)


# ==================== 审计日志（canonical: backend.models.audit） ====================
from backend.models.audit import AuditLog  # noqa: F401 — re-export for back-compat


# ==================== 定时任务（canonical: backend.models.schedule） ====================
from backend.models.schedule import TaskSchedule  # noqa: F401 — re-export for back-compat
