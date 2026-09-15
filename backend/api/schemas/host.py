from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    # #908：known_hosts 已有不同主机密钥时，默认拒绝静默替换；
    # 管理员显式置 true 才换钥（审计记录新旧指纹）
    replace_host_key: bool = False


class HostUpdate(BaseModel):
    """#950: PUT 以提交字段为准——未提交字段（含密钥认证配置）保持不变。

    编辑表单只发用户改动过的字段（如仅改名称）；复用 HostCreate 会让
    缺省默认值（password/None）无条件覆盖既有密钥认证配置。
    """

    name: Optional[str] = None
    ip: Optional[str] = None
    ssh_port: Optional[int] = None
    ssh_user: Optional[str] = None
    ssh_auth_type: Optional[str] = None
    ssh_key_path: Optional[str] = None
    ssh_password: Optional[str] = None
    ssh_known_hosts_path: Optional[str] = None
    # #908：同 HostCreate.replace_host_key
    replace_host_key: bool = False


class HostWatcherAdminStatePatch(BaseModel):
    watcher_admin_active: bool


class HostInstallOptions(BaseModel):
    """Agent 首次安装的目标路径下传（可选，全部缺省时用 ansible group_vars 默认值）。

    站点安装（tools/site_config/agents.py）用这两个键把 site.yaml 声明的路径
    传给既有 ansible 执行链，避免安装出与站点配置不一致的目录布局。
    值由服务端校验（绝对路径、无空白/占位符）并随安装审计留痕。
    """

    # 安装目录 → ansible agent_install_dir（默认 /opt/stability-test-agent）
    agent_install_root: Optional[str] = None
    # 机器本地 AEE 第一落点 → ansible agent_local_aee_root（写入 STP_AEE_LOCAL_ROOT）
    agent_local_aee_root: Optional[str] = None
    # 中心存储挂载点 → ansible agent_nfs_root（写入 STP_AEE_NFS_ROOT）
    agent_nfs_root: Optional[str] = None


class HostInstallIn(BaseModel):
    """POST /hosts/{id}/install 请求体：无字段时与旧的无请求体调用等价。"""

    install_options: Optional[HostInstallOptions] = None


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
    # ADR-0038 D6-(a)：交付面字段名 `agent_instance_id`，ORM 属性名为
    # `last_agent_instance_id`——用 validation_alias 桥接（populate_by_name
    # 让两种写法都能构造，先例见 schemas/schedule.py）。
    model_config = ConfigDict(populate_by_name=True)

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
    # Agent version display (protocol semver + git revision traceability)
    agent_protocol_version: Optional[str] = None
    agent_code_revision: Optional[str] = None
    # ADR-0040 D2/P2：Agent 上报的部署身份（内容一致性比对源；站点验收读它）
    agent_artifact_digest: Optional[str] = None
    agent_resources_digest: Optional[str] = None
    expected_code_revision: Optional[str] = None
    agent_code_deployed: Optional[str] = None
    agent_code_deployed_at: Optional[str] = None
    agent_code_sync_status: Literal["unknown", "matched", "drift", "pending"] = "unknown"
    # ADR-0038 D6-(a)：身份当前值交付面（换机 = 同 IP 同 id → unretire，
    # boot_id / agent_instance_id 变化在详情与审计可见）。
    boot_id: Optional[str] = None
    agent_instance_id: Optional[str] = Field(
        default=None, validation_alias="last_agent_instance_id",
    )
    # ADR-0038 D1/D4：退役生命周期（retired_at 非空即退役，与 status 正交）
    # 与「已退役但仍在心跳」单次告警的去重时间戳。
    retired_at: Optional[datetime] = None
    retired_by: Optional[str] = None
    retire_reason: Optional[str] = None
    retire_alerted_at: Optional[datetime] = None

    @field_validator('extra', 'mount_status', mode='before')
    @classmethod
    def _coerce_none_to_dict(cls, v):
        return v or {}


class HostRetireIn(BaseModel):
    """ADR-0038 D2：退役请求体——原因必填（审计 who/when/reason 的 reason）。"""

    retire_reason: str = Field(min_length=1)


class HostUnretireIn(BaseModel):
    """ADR-0038 D2：解除退役请求体——原因同样必填（与 retire 审计对称）。"""

    retire_reason: str = Field(min_length=1)


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
    agent_code_revision: Optional[str] = None  # git short SHA from agent VERSION file
    agent_artifact_digest: str = ""  # ADR-0040 D2: deployed artifact digest from agent ARTIFACT_DIGEST file
    agent_resources_digest: str = ""  # ADR-0040 P2 (#1963): host-resources identity from ARTIFACT_DIGEST_RESOURCES

    @field_validator('host_id', mode='before')
    @classmethod
    def coerce_str(cls, v):
        return str(v)
