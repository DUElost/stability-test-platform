from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from backend.api.schemas.base import ORMBaseModel


class HostCreate(BaseModel):
    name: str
    ip: str
    ssh_port: int = 22
    ssh_user: Optional[str] = None
    ssh_auth_type: str = "password"
    ssh_key_path: Optional[str] = None
    ssh_password: Optional[str] = None
    ssh_known_hosts_path: Optional[str] = None


class HostWatcherAdminStatePatch(BaseModel):
    watcher_admin_active: bool


class HostActiveJob(BaseModel):
    """ADR-0021: per-host snapshot of an active Job for the hot-update gate."""
    id: int
    plan_run_id: Optional[int] = None
    plan_id: Optional[int] = None
    device_id: int
    status: str
    started_at: Optional[datetime] = None
    abort_pending: bool = False  # v3: PlanRun.run_context 含 abort_requested


class HostOut(ORMBaseModel):
    id: str
    name: Optional[str] = None
    ip: Optional[str] = None
    ssh_port: Optional[int] = 22
    ssh_user: Optional[str] = None
    ssh_auth_type: Optional[str] = None
    status: str
    watcher_admin_active: bool = True
    last_heartbeat: Optional[datetime] = None
    extra: Dict[str, Any] = {}
    mount_status: Dict[str, Any] = {}
    # ADR-0019 Phase 3c: 结构化 capacity/health
    capacity: Optional[Dict[str, Any]] = None
    health: Optional[Dict[str, Any]] = None
    # ADR-0021: hot-update guard — populated only on GET /hosts/{id}.
    active_job_count: int = 0
    active_jobs: List[HostActiveJob] = Field(default_factory=list)
    # ssh-keyscan result on create/update ("ok" | "failed: <reason>" | None).
    host_key_trust: Optional[str] = None
    # 安装态信号（非 HostStatus）：曾成功安装 / 有过心跳 / 有 agent_version。
    # 用于区分「从未安装」与「已装但 OFFLINE」，避免 UI 误显示「首次安装」。
    agent_installed: bool = False
    agent_installed_at: Optional[str] = None

    @field_validator('extra', 'mount_status', mode='before')
    @classmethod
    def _coerce_none_to_dict(cls, v):
        return v or {}


class HostLiteOut(ORMBaseModel):
    id: str
    name: Optional[str] = None
    ip: Optional[str] = None
    status: str


class HeartbeatIn(BaseModel):
    host_id: str
    status: Literal["ONLINE", "OFFLINE", "DEGRADED"]
    script_catalog_version: str = ""
    mount_status: Dict[str, Any] = Field(default_factory=dict)
    extra: Dict[str, Any] = Field(default_factory=dict)
    host: Optional[Dict[str, Any]] = None
    devices: List[Dict[str, Any]] = Field(default_factory=list)
    capacity: Optional[Dict[str, Any]] = None  # ADR-0019 Phase 1
    health: Optional[Dict[str, Any]] = None    # ADR-0019 Phase 3c
    agent_instance_id: str = ""   # ADR-0019 Phase 3a
    boot_id: str = ""             # ADR-0019 Phase 3a
    agent_version: Optional[str] = None  # ADR-0020 preflight data source

    @field_validator('host_id', mode='before')
    @classmethod
    def coerce_str(cls, v):
        return str(v)
