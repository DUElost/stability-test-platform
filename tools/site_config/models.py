from __future__ import annotations

import ipaddress
import re
import unicodedata
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Annotated, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if TYPE_CHECKING:
    # #2268：`typing.Self` 是 3.11+ 才有的名字，而安装器 venv 与后端 venv 都由
    # `/usr/bin/python3` 建（`deploy/lib/deploy-common.sh:83`、
    # `tools/site_config/stages.py:603`）——支持矩阵里的 Ubuntu 22.04 自带 3.10，
    # 顶层 import 它就直接 ImportError，且 preflight 事先全绿。矩阵既然承诺 22.04，
    # import 链就必须能在 3.10 成立：本文件有 `from __future__ import annotations`，
    # 校验器的 `-> Self` 只是字符串，收进 TYPE_CHECKING 后运行期一行都不执行。
    from typing import Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError


def invalid(code: str) -> PydanticCustomError:
    return PydanticCustomError(code, code)


def plain_text(value: str) -> str:
    if not value.strip() or any(unicodedata.category(char).startswith("C") for char in value):
        raise invalid("invalid_text")
    return value


def absolute_path(value: str) -> str:
    if (
        not re.fullmatch(r"/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", value)
        or any(part in {".", ".."} for part in value.split("/"))
        or len(value) > 4096
    ):
        raise invalid("invalid_path")
    return value


def dedicated_path(value: str) -> str:
    absolute_path(value)
    protected_roots = {
        "bin", "boot", "dev", "etc", "lib", "lib32", "lib64", "proc", "run", "sbin", "sys", "usr",
    }
    shared_roots = {
        "/home", "/media", "/mnt", "/opt", "/root", "/srv", "/tmp", "/var",
        "/var/cache", "/var/lib", "/var/log", "/var/tmp",
    }
    if value.split("/")[1] in protected_roots or value in shared_roots:
        raise invalid("dedicated_path_required")
    return value


def paths_overlap(first: str, second: str) -> bool:
    first_path, second_path = PurePosixPath(first), PurePosixPath(second)
    return first_path.is_relative_to(second_path) or second_path.is_relative_to(first_path)


def target_name(value: str) -> str:
    if not value or len(value) > 253 or "%" in value:
        raise invalid("invalid_target")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        hostname = value.removesuffix(".").lower()
        if hostname == "localhost" or hostname.endswith(".localhost"):
            raise invalid("invalid_target") from None
        if re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*", hostname):
            raise invalid("invalid_target") from None
        if not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in hostname.split(".")
        ):
            raise invalid("invalid_target") from None
        return hostname
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address.is_loopback or address.is_unspecified or address.is_multicast or address.is_link_local:
        raise invalid("invalid_target")
    return address.compressed


def web_url(value: str, *, origin: bool) -> str:
    if (
        not value.isascii()
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
        or any(char in value for char in "\\@?#%${}<>`\"'")
        or len(value) > 2048
    ):
        raise invalid("invalid_url")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise invalid("invalid_url") from None
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise invalid("invalid_url")
    try:
        target_name(hostname)
    except PydanticCustomError:
        raise invalid("invalid_url") from None
    authority = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        if not 1 <= port <= 65535:
            raise invalid("invalid_url")
        authority += f":{port}"
    if parsed.netloc.lower() != authority.lower():
        raise invalid("invalid_url")
    if origin and parsed.path not in {"", "/"}:
        raise invalid("origin_required")
    if parsed.path not in {"", "/"}:
        absolute_path(parsed.path.removesuffix("/"))
    return value


def origin_url(value: str) -> str:
    return web_url(value, origin=True)


def documentation_url(value: str) -> str:
    return web_url(value, origin=False)


Name = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")]
LogicalKey = Annotated[
    str, Field(min_length=1, max_length=63, pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$"),
]
Username = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[a-z_][a-z0-9_-]*$")]
Text = Annotated[str, Field(min_length=1, max_length=160), AfterValidator(plain_text)]
AbsolutePath = Annotated[str, AfterValidator(absolute_path)]
DedicatedPath = Annotated[str, AfterValidator(dedicated_path)]
Target = Annotated[str, AfterValidator(target_name)]
Origin = Annotated[str, AfterValidator(origin_url)]
DocumentationURL = Annotated[str, AfterValidator(documentation_url)]


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class SiteIdentity(ConfigModel):
    id: LogicalKey
    display_name: Text
    timezone: str

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise invalid("invalid_timezone") from None
        return value


class SharedPlatform(ConfigModel):
    os_family: Literal["linux"]
    cpu_arch: Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[a-z0-9][a-z0-9_-]*$")]
    service_manager: Literal["systemd"]

    @field_validator("cpu_arch")
    @classmethod
    def explicit_architecture(cls, value: str) -> str:
        if value in {"unknown", "null", "tbd", "todo", "auto", "changeme"}:
            raise invalid("explicit_architecture_required")
        return value


class LinuxDistribution(ConfigModel):
    distribution: Literal["debian", "ubuntu"]
    version: str

    @field_validator("version")
    @classmethod
    def explicit_version(cls, value: str, info: ValidationInfo) -> str:
        distribution = info.data.get("distribution")
        pattern = r"[1-9][0-9]*(?:\.[0-9]+)?" if distribution == "debian" else r"[0-9]{2}\.(?:04|10)(?:\.[0-9]+)?"
        if not re.fullmatch(pattern, value):
            raise invalid("invalid_os_version")
        return value


class Network(ConfigModel):
    dependency_mode: Literal["offline", "controlled_mirror"]


class ControlPlane(ConfigModel):
    target: Target
    os: LinuxDistribution
    # 本地模式（安装器在控制面本机执行）不需要控制面 SSH；仅远端编排时才要求。
    ssh_user: Username | None = None
    ssh_credential_ref: Name | None = None
    deploy_root: DedicatedPath
    deploy_user: Username
    public_url: Origin
    security_profile: Literal["production", "internal"]
    tls_ref: Name | None = None

    @field_validator("deploy_user")
    @classmethod
    def non_root_service_user(cls, value: str) -> str:
        if value == "root":
            raise invalid("non_root_service_user_required")
        return value

    @model_validator(mode="after")
    def security_consistency(self) -> Self:
        https = urlsplit(self.public_url).scheme == "https"
        if self.security_profile == "production" and not https:
            raise invalid("production_https_required")
        # internal 是 ADR-0024 v1.1 的「无 TLS 内网」豁免位：声明 https 即豁免前提消失，
        # 必须走 production 模板对（TLS nginx + secure cookie）。模板对只按 profile 选择，
        # internal + https 会静默装出 listen 80 与 AUTH_COOKIE_SECURE=0。
        if self.security_profile == "internal" and https:
            raise invalid("internal_https_profile_conflict")
        if https and self.tls_ref is None:
            raise invalid("tls_reference_required")
        if not https and self.tls_ref is not None:
            raise invalid("http_tls_conflict")
        return self


class Storage(ConfigModel):
    # local_mount：本机磁盘的子树（如 bind 到声明路径），没有远端身份与管理面。
    provisioning: Literal["managed_linux", "existing_share", "local_mount"]
    protocol: Literal["nfs", "cifs"] | None = None
    target: Target | None = None
    os: LinuxDistribution | None = None
    ssh_user: Username | None = None
    ssh_credential_ref: Name | None = None
    share: str | None = None
    credential_ref: Name | None = None
    mount_path: DedicatedPath
    # 把本机子树以 NFS 导出给本站 Agent（Agent 的 STP_AEE_NFS_ROOT 才有意义）。
    # 仅 local_mount 可开：远端分享/受管存储由对方导出，站点再导出会形成两套来源。
    export_to_agents: bool = False

    @model_validator(mode="after")
    def storage_consistency(self) -> Self:
        management_fields = (self.os, self.ssh_user, self.ssh_credential_ref)
        if self.export_to_agents and self.provisioning != "local_mount":
            raise invalid("storage_export_conflict")
        if self.provisioning == "local_mount":
            # 本机路径不是「分享」：写进 target/protocol/share 只会让 site.yaml 说谎
            if any(value is not None for value in (
                *management_fields, self.target, self.protocol, self.share, self.credential_ref,
            )):
                raise invalid("local_mount_fields_conflict")
            return self
        if self.target is None or self.protocol is None or self.share is None:
            raise invalid("storage_share_required")
        if self.provisioning == "managed_linux" and any(value is None for value in management_fields):
            raise invalid("storage_management_required")
        if self.provisioning == "existing_share" and any(value is not None for value in management_fields):
            raise invalid("existing_share_management_conflict")
        if self.protocol == "nfs":
            dedicated_path(self.share)
            if self.credential_ref is not None:
                raise invalid("nfs_credential_conflict")
        else:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", self.share):
                raise invalid("invalid_cifs_share")
            if self.credential_ref is None:
                raise invalid("cifs_credential_required")
        return self


class Agent(ConfigModel):
    key: LogicalKey
    target: Target
    os: LinuxDistribution
    ssh_user: Username
    ssh_credential_ref: Name
    install_root: DedicatedPath
    local_aee_root: DedicatedPath
    #: SSH 端口（逐主机覆盖，inventory 的 `ansible_port`）。
    #: #2283：此前 inventory 解析后被丢弃、Host 行恒以 22 建立——sshd 不在 22 的
    #: 主机会连错服务（通常安装中途失败），且文档一直把它列为受支持的覆盖项。
    ssh_port: Annotated[int, Field(ge=1, le=65535)] = 22

    @model_validator(mode="after")
    def separate_local_data(self) -> Self:
        if paths_overlap(self.install_root, self.local_aee_root):
            raise invalid("agent_local_path_overlap")
        return self


class Dependencies(ConfigModel):
    database_ref: Name
    redis_ref: Name
    tools_profile: Name


class Security(ConfigModel):
    jwt_key_ref: Name
    agent_secret_ref: Name
    ssh_encryption_key_ref: Name
    initial_admin_ref: Name


class Release(ConfigModel):
    bundle: AbsolutePath
    manifest: AbsolutePath | None = None
    expected_release: Annotated[
        str, Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"),
    ]

    @field_validator("expected_release")
    @classmethod
    def explicit_release(cls, value: str) -> str:
        if value.lower() in {"latest", "main", "master", "head", "unknown", "tbd", "todo"}:
            raise invalid("explicit_release_required")
        return value


class Navigation(ConfigModel):
    contact: Text
    documentation_url: DocumentationURL


class Monitoring(ConfigModel):
    """站点本地监控栈（#2197）：/storage 页的数据源。

    Prometheus 只听本机回环，端口与后端默认 ``STP_PROMETHEUS_URL``
    （``http://127.0.0.1:9091``）对齐——装完即出数据，后端零改环境。
    """

    enabled: bool = False
    prometheus_port: Annotated[int, Field(ge=1024, le=65535)] = 9091


class SiteConfig(ConfigModel):
    schema_version: Annotated[int, Field(ge=1, le=1)]
    site: SiteIdentity
    platform: SharedPlatform
    network: Network
    control_plane: ControlPlane
    storage: Storage
    # 允许为空：先把控制面装好、Agent 随后按 inventory 接入（S5 跳过并提示）。
    agents: Annotated[list[Agent], Field(max_length=1000)]
    dependencies: Dependencies
    security: Security
    release: Release
    navigation: Navigation
    # 可选段：旧站点输入（无该段）仍然合法，缺省不装监控栈。
    monitoring: Monitoring = Field(default_factory=Monitoring)

    @model_validator(mode="after")
    def site_consistency(self) -> Self:
        if len({agent.key for agent in self.agents}) != len(self.agents):
            raise invalid("duplicate_agent_key")
        if len({agent.target for agent in self.agents}) != len(self.agents):
            raise invalid("duplicate_agent_target")
        # STP_SCRIPT_RUNTIME_ROOT 是站点级单值（S2 渲染），异构安装根会静默取错路径
        if len({agent.install_root for agent in self.agents}) > 1:
            raise invalid("agent_install_root_mismatch")
        roots = [self.control_plane.deploy_root]
        roots.extend(root for agent in self.agents for root in (agent.install_root, agent.local_aee_root))
        if any(paths_overlap(root, self.storage.mount_path) for root in roots):
            raise invalid("shared_path_overlap")
        # 声明的 target 必须互不相同：被远端管理的角色同机=双重管理。
        # local_mount 没有 target（本机子树），因此天然不占槽位。
        targets = [self.control_plane.target, *(agent.target for agent in self.agents)]
        if self.storage.target is not None:
            targets.append(self.storage.target)
        if len(set(targets)) != len(targets):
            raise invalid("role_target_collision")
        references = [
            self.dependencies.database_ref, self.dependencies.redis_ref,
            self.security.jwt_key_ref, self.security.agent_secret_ref,
            self.security.ssh_encryption_key_ref, self.security.initial_admin_ref,
        ]
        if len(set(references)) != len(references):
            raise invalid("secret_reference_collision")
        return self


SAFE_FIELD_NAMES = frozenset(
    field_name
    for model in (
        SiteConfig, SiteIdentity, SharedPlatform, LinuxDistribution, Network,
        ControlPlane, Storage, Agent, Dependencies, Security, Release, Navigation,
    )
    for field_name in model.model_fields
)
