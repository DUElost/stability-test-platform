"""Ansible-style inventory → site Agent list (I5.5).

The inventory is the operator-facing way to add Agents: one line per host in a
file kept outside the repository.  Credentials default to a single shared
binding (``agent_ssh``) so onboarding a host is "add a line", while a host may
still pin its own ``ssh_credential_ref``.

Only the standard Ansible INI shape is accepted, so the same file can drive both
``deploy/agent/install.sh`` and the Ansible playbooks:

    [stp_agents]
    192.0.2.11 ansible_user=agentops ansible_password=...
    192.0.2.12 ansible_user=agentops ansible_password=...

    [stp_agents:vars]
    install_root=/opt/stability-test-agent
    local_aee_root=/var/stp-aee

Every host defaults to the same credential ref, so one shared credential covers
the fleet.  A host that needs a *different* credential (another user, another
kind, another secret) must name its own ``ssh_credential_ref``: silently
reusing a ref for two different secrets would overwrite one host's credential.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from pydantic import ValidationError

from .models import SiteConfig
from .validation import ConfigValidationError, schema_checks

AGENT_GROUPS = ("stp_agents", "linux_hosts")
DEFAULT_INSTALL_ROOT = "/opt/stability-test-agent"
DEFAULT_LOCAL_AEE_ROOT = "/var/stp-aee"
DEFAULT_CREDENTIAL_REF = "agent_ssh"

TEMPLATE = """# 站点 Agent 清单（不入仓库；deploy/agent/install.sh 默认读 ~/hosts.ini）
#
# 每台一行：<host> [ansible_host=..] ansible_user=<ssh 用户> \
#           (ansible_password=<口令> | ansible_ssh_private_key_file=<私钥路径>)
# 可选：ansible_port（→ Agent.ssh_port）、agent_key、install_root、local_aee_root、
#       ssh_credential_ref
# 组级默认写在 [stp_agents:vars]；口令/私钥不放进仓库。

[stp_agents]
# 192.0.2.11 ansible_host=192.0.2.11 ansible_user=ops ansible_password=CHANGEME

[stp_agents:vars]
install_root=/opt/stability-test-agent
local_aee_root=/var/stp-aee
"""


class InventoryError(ValueError):
    """``detail`` 只允许出现主机名、键名与凭证 ref——绝不放秘密值。"""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}{': ' + detail if detail else ''}")


def _derived_key(target: str) -> str:
    """IP/主机名 → 合法逻辑键：首字符必须是字母（``LogicalKey``），故加 ``agent-`` 前缀。"""
    slug = re.sub(r"[^a-z0-9]+", "-", target.lower()).strip("-")
    return f"agent-{slug}"[:63].rstrip("-")


def _pairs(tokens: list[str]) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for token in tokens:
        if "=" not in token:
            # 绝不回显这个 token：没有 "=" 的裸词本身就是秘密（口令里带空格就会这样）
            raise InventoryError("inventory_shape", "token without '=' (key=value required)")
        key, _, value = token.partition("=")
        pairs[key.strip()] = value.strip()
    return pairs


def _parse_port(raw, target: str) -> int:
    """``ansible_port`` → int 端口；越界/非数字在**解析期**拒绝（#2283）。

    此前该值解析后被 pop 丢弃，Host 行恒以 22 建立——sshd 不在 22 的主机会连错服务。
    现在带到 Host payload；非法值不能等到建行时才发现，故在此 fail-closed。
    """
    try:
        port = int(raw)
    except (TypeError, ValueError):
        raise InventoryError("inventory_shape", f"{target}: ansible_port") from None
    if not 1 <= port <= 65535:
        raise InventoryError("inventory_shape", f"{target}: ansible_port")
    return port


def parse_inventory(path: str | Path) -> tuple[list[dict], dict[str, dict[str, str]]]:
    """Return (agent entries, credential material keyed by credential ref)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        raise InventoryError("inventory_file") from None
    group_vars: dict[str, str] = {}
    entries: list[dict] = []
    credentials: dict[str, dict[str, str]] = {}
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("["):
            if not line.endswith("]"):
                raise InventoryError("inventory_shape", "section header")
            section = line[1:-1].strip()
            continue
        if section.endswith(":vars"):
            group = section[: -len(":vars")]
            if group in AGENT_GROUPS:
                group_vars.update(_pairs(line.split()))
            continue
        if section.endswith(":children") or section not in AGENT_GROUPS:
            continue
        tokens = line.split()
        host, pairs = tokens[0], _pairs(tokens[1:])
        merged = dict(group_vars)
        merged.update(pairs)
        target = merged.pop("ansible_host", "") or host
        key = merged.pop("agent_key", "")
        if key and not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", key[:63]):
            # 显式键名同样受 LogicalKey 约束；这里先拒，避免落到模型的通用报文
            raise InventoryError("inventory_shape", f"{target}: agent_key")
        entry = {
            "key": key or _derived_key(target),
            "target": target,
            "os_distribution": merged.pop("os_distribution", "debian"),
            "os_version": merged.pop("os_version", "13"),
            "ssh_user": merged.pop("ansible_user", ""),
            "ssh_credential_ref": merged.pop("ssh_credential_ref", DEFAULT_CREDENTIAL_REF),
            "install_root": merged.pop("install_root", DEFAULT_INSTALL_ROOT),
            "local_aee_root": merged.pop("local_aee_root", DEFAULT_LOCAL_AEE_ROOT),
            "ssh_port": _parse_port(merged.pop("ansible_port", 22), target),
            "_password": merged.pop("ansible_password", ""),
            "_key_path": merged.pop("ansible_ssh_private_key_file", ""),
        }
        if merged:
            raise InventoryError("inventory_shape", ",".join(sorted(merged))[:64])
        if not entry["ssh_user"]:
            raise InventoryError("inventory_user_missing", target)
        ref = entry["ssh_credential_ref"]
        material = {"USERNAME": entry["ssh_user"]}
        if entry["_password"]:
            material["PASSWORD"] = entry["_password"]
        elif entry["_key_path"]:
            material["PRIVATE_KEY_PATH"] = entry["_key_path"]
        else:
            raise InventoryError("inventory_credential_missing", target)
        previous = credentials.get(ref)
        if previous is not None and previous != material:
            # 同 ref 不同凭据：宁可拒绝，也不要把某台机器的凭据写错
            raise InventoryError("inventory_credential_conflict", f"{target} -> {ref}")
        credentials[ref] = material
        entry.pop("_password")
        entry.pop("_key_path")
        entries.append(entry)
    if not entries:
        raise InventoryError("inventory_empty")
    return entries, credentials


def merge_agents(config: SiteConfig, inventory_path: str | Path) -> SiteConfig:
    """Merge inventory hosts into the site's Agent list (inventory wins per target).

    The result goes back through the *same* site model, so an inventory cannot
    introduce a shape the rest of the installer would reject — e.g. mixed
    install roots.  Model rejections surface as ordinary configuration checks
    (not a traceback), because the operator's next action is the same.
    """
    entries, _credentials = parse_inventory(inventory_path)
    derived = []
    for entry in entries:
        entry = dict(entry)
        derived.append({
            "key": entry["key"],
            "target": entry["target"],
            "os": {"distribution": entry.pop("os_distribution"), "version": entry.pop("os_version")},
            "ssh_user": entry["ssh_user"],
            "ssh_credential_ref": entry["ssh_credential_ref"],
            "install_root": entry["install_root"],
            "local_aee_root": entry["local_aee_root"],
        })
    declared = {agent["target"]: agent for agent in config.model_dump()["agents"]}
    for agent in derived:
        declared[agent["target"]] = agent
    payload = config.model_dump()
    payload["agents"] = list(declared.values())
    try:
        return SiteConfig.model_validate(payload)
    except ValidationError as error:
        raise ConfigValidationError(schema_checks(error, check_id="install.inventory")) from None


def materialize_bindings(
    inventory_path: str | Path,
    bindings_dir: str | Path,
    *,
    dry_run: bool = False,
) -> list[str]:
    """Write the SSH bindings the inventory implies (0600, owner-only directory)."""
    _entries, credentials = parse_inventory(inventory_path)
    directory = Path(bindings_dir)
    if not dry_run:
        try:
            info = os.lstat(directory)
        except OSError:
            raise InventoryError("binding_dir") from None
        if not (info.st_mode & 0o040000) or info.st_mode & 0o077:
            raise InventoryError("binding_dir")
    written: list[str] = []
    for ref, material in sorted(credentials.items()):
        lines = "".join(f"{key}={value}\n" for key, value in sorted(material.items()))
        if dry_run:
            written.append(ref)
            continue
        path = directory / ref
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(lines)
        os.chmod(path, 0o600)
        written.append(ref)
    return written
