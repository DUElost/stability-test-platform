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
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from ..core.database import Base


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


class TaskTemplate(Base):
    __tablename__ = "task_templates"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), unique=True, nullable=False)
    type = Column(String(32), nullable=False)
    default_params = Column(JSON, default=dict)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    tasks = relationship("Task", back_populates="template")


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    type = Column(String(32), nullable=False)
    template_id = Column(Integer, ForeignKey("task_templates.id"))
    params = Column(JSON, default=dict)
    target_device_id = Column(Integer, ForeignKey("devices.id"))
    status = Column(Enum(TaskStatus), default=TaskStatus.PENDING, nullable=False)
    priority = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    template = relationship("TaskTemplate", back_populates="tasks")
    runs = relationship("TaskRun", back_populates="task")


class TaskRun(Base):
    __tablename__ = "task_runs"

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    host_id = Column(Integer, ForeignKey("hosts.id"), nullable=False)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False)
    status = Column(Enum(RunStatus), default=RunStatus.QUEUED, nullable=False)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    last_heartbeat_at = Column(DateTime)
    exit_code = Column(Integer)
    error_code = Column(String(64))
    error_message = Column(Text)
    log_summary = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

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
