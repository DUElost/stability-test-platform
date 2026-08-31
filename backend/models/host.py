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
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from backend.core.database import Base


class Host(Base):
    __tablename__ = "host"

    id                   = Column(String(64), primary_key=True)
    # hostname 唯一约束显式命名 host_hostname_key（对齐迁移链，schema-sync
    # 不因自动命名漂移报警）
    hostname             = Column(String(256), nullable=False)
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

    __table_args__ = (
        UniqueConstraint("hostname", name="host_hostname_key"),
        # 心跳超时巡检（reconciler / watchdog）按 last_heartbeat 排序扫描
        Index("idx_host_last_heartbeat", "last_heartbeat"),
    )


class Device(Base):
    __tablename__ = "device"

    id         = Column(Integer, primary_key=True)
    serial     = Column(String(128), nullable=False, unique=True)
    host_id    = Column(String(64), ForeignKey("host.id", ondelete="CASCADE", onupdate="CASCADE"))
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

    host = relationship("backend.models.host.Host", foreign_keys=[host_id])

    __table_args__ = (
        Index("idx_device_host", "host_id"),
        # v2.5 D10：归属派生读路径（_summary_rows / _platforms_map /
        # inventory / 派发推断 / suite 门禁）的 join 键，随
        # f8a9b0c1d2e3 建（避免 model↔migration 漂移被 schema-sync 拦）。
        Index("idx_device_model", "model"),
    )
