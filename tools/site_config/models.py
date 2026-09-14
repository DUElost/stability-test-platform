from __future__ import annotations

import ipaddress
import re
import unicodedata
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
    ssh_user: Username
    ssh_credential_ref: Name
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
        if https and self.tls_ref is None:
            raise invalid("tls_reference_required")
        if not https and self.tls_ref is not None:
            raise invalid("http_tls_conflict")
        return self


class Storage(ConfigModel):
    provisioning: Literal["managed_linux", "existing_share"]
    protocol: Literal["nfs", "cifs"]
    target: Target
    os: LinuxDistribution | None = None
    ssh_user: Username | None = None
    ssh_credential_ref: Name | None = None
    share: str
    credential_ref: Name | None = None
    mount_path: DedicatedPath

    @model_validator(mode="after")
    def storage_consistency(self) -> Self:
        management_fields = (self.os, self.ssh_user, self.ssh_credential_ref)
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


class SiteConfig(ConfigModel):
    schema_version: Annotated[int, Field(ge=1, le=1)]
    site: SiteIdentity
    platform: SharedPlatform
    network: Network
    control_plane: ControlPlane
    storage: Storage
    agents: Annotated[list[Agent], Field(min_length=1, max_length=1000)]
    dependencies: Dependencies
    security: Security
    release: Release
    navigation: Navigation

    @model_validator(mode="after")
    def site_consistency(self) -> Self:
        if len({agent.key for agent in self.agents}) != len(self.agents):
            raise invalid("duplicate_agent_key")
        if len({agent.target for agent in self.agents}) != len(self.agents):
            raise invalid("duplicate_agent_target")
        roots = [self.control_plane.deploy_root]
        roots.extend(root for agent in self.agents for root in (agent.install_root, agent.local_aee_root))
        if any(paths_overlap(root, self.storage.mount_path) for root in roots):
            raise invalid("shared_path_overlap")
        targets = [self.control_plane.target, self.storage.target, *(agent.target for agent in self.agents)]
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
