from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from backend.core.database import Base


class Host(Base):
    __tablename__ = "host"

    id                   = Column(String(64), primary_key=True)
    hostname             = Column(String(256), nullable=False, unique=True)
    ip_address           = Column(String(64))
    script_catalog_version = Column(String(64))
    last_heartbeat       = Column(DateTime(timezone=True))
    cpu_quota            = Column(Integer, nullable=False, default=2)
    status               = Column(String(32), nullable=False, default="OFFLINE")
    watcher_admin_active = Column(Boolean, nullable=False, default=True)
    created_at           = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    # 迁移补齐字段
    # #101: name 与 hostname 恒同值（hostname 已唯一），显式化；ip 唯一防止
    # 同一物理机登记两行导致心跳/容量/租约按 id 结算分叉。
    name          = Column(String(128), nullable=True, unique=True)
    ip            = Column(String(64), nullable=True, unique=True)
    ssh_port      = Column(Integer, default=22, nullable=True)
    ssh_user      = Column(String(64), nullable=True)
    ssh_auth_type = Column(String(32), default="password", nullable=True)
    ssh_key_path  = Column(String(256), nullable=True)
    ssh_password_enc = Column(String(1024), nullable=True)
    ssh_known_hosts_path = Column(String(512), nullable=True)
    extra         = Column(JSON, default=dict, nullable=True)
    mount_status  = Column(JSON, default=dict, nullable=True)
    updated_at    = Column(DateTime(timezone=True), onupdate=lambda: datetime.now(timezone.utc), nullable=True)
    boot_id       = Column(String(64), nullable=False, default="")           # ADR-0019 Phase 3a
    last_agent_instance_id = Column(String(64), nullable=False, default="")  # ADR-0019 Phase 3a


class Device(Base):
    __tablename__ = "device"

    id         = Column(Integer, primary_key=True)
    serial     = Column(String(128), nullable=False, unique=True)
    host_id    = Column(String(64), ForeignKey("host.id"))
    model      = Column(String(128))
    platform   = Column(String(64))
    tags       = Column(JSONB, nullable=False, default=list)
    status     = Column(String(32), nullable=False, default="OFFLINE")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    # 迁移补齐字段
    last_seen           = Column(DateTime(timezone=True), nullable=True)
    adb_state           = Column(String(32), nullable=True)
    adb_connected       = Column(Boolean, default=False, nullable=True)
    battery_level       = Column(Integer, nullable=True)
    battery_temp        = Column(Integer, nullable=True)
    temperature         = Column(Integer, nullable=True)
    wifi_rssi           = Column(Integer, nullable=True)
    wifi_ssid           = Column(String(128), nullable=True)
    network_latency     = Column(Float, nullable=True)
    cpu_usage           = Column(Float, nullable=True)
    mem_total           = Column(BigInteger, nullable=True)
    mem_used            = Column(BigInteger, nullable=True)
    disk_total          = Column(BigInteger, nullable=True)
    disk_used           = Column(BigInteger, nullable=True)
    build_display_id    = Column(String(256), nullable=True)
    hardware_updated_at = Column(DateTime(timezone=True), nullable=True)
    lease_generation    = Column(Integer, nullable=False, default=0)   # ADR-0019 Phase 1
    extra               = Column(JSON, default=dict, nullable=True)
    # ADR-0029 归属（P1 M-a）：NULL = 迁移期瞬态，M-c 回填后归零（完成标准）。
    # 迁移完成后不存在「公共池」域——未识别设备归 LEGACY 项目。
    project_id          = Column(Integer, ForeignKey("test_project.id"), nullable=True)
    # ADR-0029 P1：人工钉住逃生阀——手工归属的设备打钉子，规则不覆盖。
    # 显式、可列出、可审计（区别于 match_models 的隐性重叠）。
    project_pinned      = Column(Boolean, nullable=False, default=False)

    host = relationship("backend.models.host.Host", foreign_keys=[host_id])
    project = relationship("backend.models.project.TestProject", foreign_keys=[project_id])

    __table_args__ = (
        Index("idx_device_host", "host_id"),
        Index("idx_device_project", "project_id"),
    )
