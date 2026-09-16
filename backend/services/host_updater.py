"""Hot-update agent code on a remote Linux host via SSH + rsync.

Uses paramiko (already a project dependency) to:
1. Package the local agent source tree into a tar.gz
2. SFTP it to the remote host's /tmp
3. SSH-exec a remote script that extracts, rsyncs to the install dir,
   and restarts the systemd service.
"""

from __future__ import annotations

import base64
import errno
import fnmatch
import io
import json
import logging
import os
import tarfile
import time
import uuid
from pathlib import Path

from backend.core.ssh_security import create_ssh_client
from backend.services.agent_env_sync import (
    agent_path_keys_to_verify,
    hot_update_env_overrides,
)

logger = logging.getLogger(__name__)

# Paths
_AGENT_SOURCE_DIR = Path(__file__).resolve().parent.parent / "agent"
_PIPELINE_SCHEMA_FILE = (
    Path(__file__).resolve().parent.parent / "schemas" / "pipeline_schema.json"
)
_REMOTE_INSTALL_DIR = "/opt/stability-test-agent"
_REMOTE_SERVICE_NAME = "stability-test-agent"

# #960：远端 tar 路径每次操作独立。固定路径下并发热更新（UI 触发 + precheck
# 回退 + 批量脚本）会互相覆盖同一个 tar —— 后传的包被前者解压，或反之。
def _remote_tar_path(prefix: str = "stp") -> str:
    return f"/tmp/stp-agent-{prefix}-{uuid.uuid4().hex}.tar.gz"

# Ansible inventory fallback for SSH credentials
_INVENTORY_PATH = Path(__file__).resolve().parent.parent.parent / "tools" / "ansible" / "inventory.ini"

# Files and dirs excluded from the tarball
# #2030：本集合 = 「部署流程实际拥有并覆盖的文件集」的排除面（ADR-0040 D1）——
# 与两条部署通道（`stp_agent_priv.py::FIXED_EXCLUDES`、Ansible
# `agent_install_excludes`）**同源**，由 `tests/test_ansible_digest_contract.py`
# 逐项锁定。venv/logs 为宿主侧目录（ADR-0040 D1 明文排除）；stp_agent_priv.py
# 由 install/update playbook 装到 /usr/local/sbin、不进安装目录；stp_schemas/
# 的 schema 经 extra_files 独立附加（walk 排除不影响 arcname 附加）。
_TAR_EXCLUDES = {
    "__pycache__",
    "tests",
    ".env.example",
    "install_agent.sh",
    "agentctl.sh",
    "DEPLOY.md",
    "stability-test-agent.service",
    "hosts.txt",
    "venv",
    "logs",
    "stp_agent_priv.py",
    "stp_schemas",
    ".deps_installed_sha",
}

# File suffixes to exclude
_TAR_EXCLUDE_SUFFIXES = (".pyc",)

# Glob 类排除（#2030）：与 rsync 通道同名的通配模式——显式常量使
# 「三处排除集同源」可被 tests/test_ansible_digest_contract.py 逐项比较。
_TAR_EXCLUDE_GLOBS = ("test_*.py",)

# ADR-0040 §5.1（P0 过渡，#1903）：压缩级 9 → 6。实测 252MB 源树打包 16.6s → 6.4s，
# 体积 125.7MB → 126.0MB（+0.3MB，内网传输代价可忽略）。终态出口 = P1 的 digest 缓存键，
# 不留双轨。
_TARBALL_COMPRESSLEVEL = 6

# ADR-0040 D1：部署载荷不含「主机态/部署态」文件——远端布局里 agent/ 下可能
# 存在的元数据（VERSION/ARTIFACT_DIGEST/.env）不进身份；resources/mtbf/ 永远
# 属主机本地（#214/#216 APK 保护语义），tarball 也不应携带（远端 rsync --delete
# 本就排除，不进包让「载荷 == 安装树」更真）。
_PAYLOAD_METADATA_EXCLUDES = {
    "VERSION", "ARTIFACT_DIGEST", "ARTIFACT_DIGEST_RESOURCES", ".env",
}


def _iter_payload_files(kind: str):
    """Yield ``(abs_path, arcname)`` over the deploy payload file set.

    tarball（``_build_tarball``）与 artifact digest（``artifact_digest.collect_artifact_entries``）
    共享同一枚举——digest 输入集 = 部署输入集由同一份代码保证（ADR-0040 D1）。
    symlink 一律跳过：tar 存链接本身而内容读取会穿透，两侧身份会分叉。

    kind 分层（ADR-0040 §5-3 P2-B，#1975）：``full`` = P1 全集（兼容语义保留）；
    ``code`` = 代码树 + schema（**不含 resources/**，分层后 ~1MB）；``resources``
    = ``resources/**``（除 ``resources/mtbf/``——永远属主机本地）。code 与
    resources 互斥、并集 == full − mtbf（契约测试守护）。

    #2030：``kind`` 必填——原默认值在 tarball（``code``）与枚举（``full``）
    两侧不对称，漏传会让「打包范围」与「身份范围」静默错配（#2019 同源风险）。
    """
    for root, dirs, files in os.walk(_AGENT_SOURCE_DIR):
        # Filter directories in-place
        dirs[:] = [d for d in dirs if d not in _TAR_EXCLUDES]

        for name in files:
            if name in _TAR_EXCLUDES:
                continue
            if name.endswith(_TAR_EXCLUDE_SUFFIXES):
                continue
            if any(fnmatch.fnmatch(name, pattern) for pattern in _TAR_EXCLUDE_GLOBS):
                continue

            full_path = os.path.join(root, name)
            if os.path.islink(full_path):
                continue
            arcname = os.path.relpath(full_path, _AGENT_SOURCE_DIR).replace(os.sep, "/")
            if arcname in _PAYLOAD_METADATA_EXCLUDES:
                continue
            if arcname == "resources/mtbf" or arcname.startswith("resources/mtbf/"):
                continue
            if kind == "code" and (
                arcname == "resources" or arcname.startswith("resources/")
            ):
                continue
            if kind == "resources" and not (
                arcname == "resources" or arcname.startswith("resources/")
            ):
                continue
            yield full_path, arcname

    if kind != "resources" and _PIPELINE_SCHEMA_FILE.is_file():
        yield _PIPELINE_SCHEMA_FILE, "stp_schemas/pipeline_schema.json"


def _build_tarball(
    kind: str, compresslevel: int = _TARBALL_COMPRESSLEVEL
) -> bytes:
    """Package the deploy payload (``kind``: code / resources / full) into a tarball.

    ``kind`` 必填（#2030）：与 ``_iter_payload_files`` 取齐，杜绝漏传时
    「打包 code、身份按 full 算」一类静默错配。
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=compresslevel) as tar:
        for full_path, arcname in _iter_payload_files(kind):
            tar.add(full_path, arcname=arcname)

    return buf.getvalue()


def _build_resources_tarball(compresslevel: int = _TARBALL_COMPRESSLEVEL) -> bytes:
    """ADR-0040 §5-3 P2-B：host-resources 载荷（resources/** 除 mtbf/）。"""
    return _build_tarball(compresslevel=compresslevel, kind="resources")


def _resolve_ssh_creds(host_ip: str) -> dict | None:
    """Look up SSH credentials from Ansible inventory by IP.

    Returns dict with keys: user, password, key_path, port, or None if not found.
    """
    if not _INVENTORY_PATH.exists():
        return None

    try:
        for line in _INVENTORY_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("["):
                continue
            # Format: <label> ansible_host=<ip> ansible_user=<u> ansible_password=<p> ...
            parts = {}
            for token in line.split():
                if "=" in token:
                    k, v = token.split("=", 1)
                    parts[k] = v

            ansible_host = parts.get("ansible_host", "")
            if ansible_host == host_ip:
                return {
                    "user": parts.get("ansible_user", "android"),
                    "password": parts.get("ansible_password", ""),
                    "key_path": parts.get("ansible_ssh_private_key_file", "")
                    or parts.get("ansible_private_key_file", ""),
                    "port": int(parts.get("ansible_port", "22")),
                }
    except Exception:
        logger.warning("inventory_parse_failed", exc_info=True)

    return None


#: 远端热更新脚本会调用的 wrapper 子命令（#2319）。脚本头按它做**能力**前置判据：
#: 缺任一项即 fail-closed（`update_agent.yml` 指引），而不是走到调用处才被 argparse
#: 拒绝（那时代码已同步、服务已重启）。守卫测试钉「脚本里的 $PRIV 调用 ⊆ 本集合」。
_REQUIRED_PRIV_SUBCOMMANDS = (
    "apply-code",
    "apply-resources",
    "install-schema",
    "write-version",
    "write-digest",
    "sync-env",
    "fix-ownership",
    "deps-marker",
    "restart",
)


_REMOTE_SCRIPT = r"""#!/bin/bash
set -e
INSTALL_DIR="{install_dir}"
SERVICE_NAME="{service_name}"
CODE_TARB_PATH="{code_tar_path}"
RESOURCES_TARB_PATH="{resources_tar_path}"
SYNC_AGENT_SECRET="{sync_agent_secret}"
AGENT_SECRET_B64="{agent_secret_b64}"
ENV_OVERRIDES_B64="{env_overrides_b64}"
ENV_PATH_KEYS_B64="{env_path_keys_b64}"
ARTIFACT_DIGEST="{artifact_digest}"
RESOURCES_DIGEST="{resources_digest}"
export PIP_INDEX_URL="{pip_index_url}"

if [ ! -d "$INSTALL_DIR" ]; then
    echo "ERROR: Agent not installed at $INSTALL_DIR"
    rm -f "$CODE_TARB_PATH" "$RESOURCES_TARB_PATH"
    exit 1
fi

CODE_TMP=$(mktemp -d)
RES_TMP=$(mktemp -d)
trap 'rm -rf "$CODE_TMP" "$RES_TMP" "$CODE_TARB_PATH" "$RESOURCES_TARB_PATH"' EXIT

# P2-B 分层（#1975）：双 tar 各自解包；空路径的层整段跳过。
if [ -n "$CODE_TARB_PATH" ]; then
    tar xzf "$CODE_TARB_PATH" -C "$CODE_TMP"
fi
if [ -n "$RESOURCES_TARB_PATH" ]; then
    tar xzf "$RESOURCES_TARB_PATH" -C "$RES_TMP"
fi

# Fix CRLF from Windows sources
find "$CODE_TMP" "$RES_TMP" -type f \( -name "*.py" -o -name "*.sh" \) \
    -exec sed -i 's/\r$//' {{}} + 2>/dev/null || true

# 提权边界（#1250/ADR-0037；D 步 #2180）：wrapper 是**唯一**提权面——legacy 裸
# sudo 面（rsync/tee/mkdir/install/chown/systemctl/任意 sh）已随 fleet 迁移退役
# （宽 sudoers 48/48 已清除；ADR-0037 §5 Revisit #1 执行完毕）。wrapper 缺失或
# selftest 失败（含子命令契约校验）即 fail-closed：带可执行指引退出、不执行任何
# 后续动作，也不回退裸 sudo。载荷不含 wrapper 本体（ADR-0037——wrapper 在安装
# 目录外、root 所有，由 install/Ansible 轨道交付）。
PRIV="/usr/local/sbin/stp-agent-priv"
if ! sudo -n "$PRIV" selftest >/dev/null 2>&1; then
    echo "ERROR: stp-agent-priv selftest failed (missing/outdated wrapper?); run tools/ansible/playbooks/update_agent.yml on this host, then retry"
    exit 1
fi
# #2319：selftest 只证 wrapper **自洽**（属主/权限 + parser↔契约表一致），不证它具备
# 本脚本要用的子命令——缺一个子命令但自洽的旧 wrapper 同样打 OK、exit 0，脚本会在
# 第一次调用处被 argparse 拒绝，而此时 apply-code/restart 等写动作**已经执行**
# （回到 #1942 修掉的半态）。故按能力集合再做一次前置判据，缺失即 fail-closed。
# 能力清单逐行输出；归一成空格分隔再按词匹配（否则 case 的 " sub " 模式匹配不到行尾）。
if ! STP_PRIV_CAPS=$(sudo -n "$PRIV" capabilities 2>/dev/null | tr '\n' ' '); then
    echo "ERROR: stp-agent-priv capabilities unavailable (missing/outdated wrapper?); run tools/ansible/playbooks/update_agent.yml on this host, then retry"
    exit 1
fi
for STP_PRIV_SUB in {required_priv_subcommands}; do
    case " $STP_PRIV_CAPS " in
        *" $STP_PRIV_SUB "*) ;;
        *)
            echo "ERROR: stp-agent-priv lacks '$STP_PRIV_SUB' (wrapper older than this update script); run tools/ansible/playbooks/update_agent.yml on this host, then retry"
            exit 1
            ;;
    esac
done
echo "STP_PRIV_MODE=wrapper"

if [ -n "$CODE_TARB_PATH" ]; then

# Capture pre-sync requirements.txt sha to detect dependency changes
OLD_REQ_SHA=$(sha256sum "$INSTALL_DIR/agent/requirements.txt" 2>/dev/null | cut -d' ' -f1 || echo "none")
# ADR-0040 D6：per-phase 计时（远端应用 / 重启探活），落控制面审计 details
APPLY_T0=$(date +%s%3N)

# Rsync into install dir
# NOTE: `--delete` 会删除远端 tarball 中不存在的目录——agent/resources/ 里
# aimonkey/、flashtool/ 随 hot-update 同步，但 resources/mtbf/ 是 host 级
# 手工布放（APK 三件套，不在仓库），必须排除，否则每次 hot-update 都会把
# MTBF 资源清掉（2026-08-20 冒烟 #214/#216「APK 不存在」根因）。
# ADR-0040 §4.3 P2 前置（#1950）：resources/ 整树加 protect（防源树删除
# 传播到 host 清掉大件），不 exclude——分发照旧（wrapper 路径同语义）。
# #2019：树的写法是 `resources/***`（尾斜杠只护目录节点，见 wrapper 里
# PROTECT_ONLY_PATHS 的说明）；filter 参数由 wrapper 的 build_apply_code_filters()
# 生成，本文件不再自持 rsync 面（#2180）。
# wrapper：固定目标 + 固定 excludes（含 mtbf protect）+ --safe-links
sudo "$PRIV" apply-code --staged "$CODE_TMP"

CODE_VERSION="{code_version}"
if [ -n "$CODE_VERSION" ]; then
    sudo "$PRIV" write-version --version "$CODE_VERSION"
fi

if [ -f "$CODE_TMP/stp_schemas/pipeline_schema.json" ]; then
    sudo "$PRIV" install-schema --file "$CODE_TMP/stp_schemas/pipeline_schema.json"
fi

if [ "$SYNC_AGENT_SECRET" = "1" ]; then
    sudo "$PRIV" sync-env --secret-b64 "$AGENT_SECRET_B64"
fi

# .env 受控键覆盖（wrapper 内部校验键名与值）
sudo "$PRIV" sync-env --overrides-b64 "$ENV_OVERRIDES_B64" --path-keys-b64 "$ENV_PATH_KEYS_B64"

# Fix ownership
sudo "$PRIV" fix-ownership

# Refresh Python dependencies when requirements.txt content changed, OR when a
# previous pip for the current requirements SHA never completed successfully
# (#948). Marker lives outside agent/ so rsync --delete cannot clear it.
DEPS_MARKER="$INSTALL_DIR/.deps_installed_sha"
DEPS_REFRESHED=0
NEW_REQ_SHA=$(sha256sum "$INSTALL_DIR/agent/requirements.txt" 2>/dev/null | cut -d' ' -f1 || echo "none")
INSTALLED_REQ_SHA=$(cat "$DEPS_MARKER" 2>/dev/null || echo "none")
NEED_PIP=0
if [ -n "$NEW_REQ_SHA" ] && [ "$NEW_REQ_SHA" != "none" ]; then
    if [ "$OLD_REQ_SHA" != "$NEW_REQ_SHA" ] || [ "$INSTALLED_REQ_SHA" != "$NEW_REQ_SHA" ]; then
        NEED_PIP=1
    fi
fi
if [ "$NEED_PIP" -eq 1 ]; then
    if [ "$OLD_REQ_SHA" != "$NEW_REQ_SHA" ]; then
        echo "INFO: requirements.txt changed ($OLD_REQ_SHA -> $NEW_REQ_SHA), running pip install"
    else
        echo "INFO: requirements.txt unchanged but deps marker missing/stale ($INSTALLED_REQ_SHA != $NEW_REQ_SHA), retrying pip install"
    fi
    "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/agent/requirements.txt" -q --disable-pip-version-check
    PIP_RC=$?
    if [ "$PIP_RC" -ne 0 ]; then
        echo "ERROR: pip install failed (exit=$PIP_RC); service NOT restarted to avoid crash"
        echo "STP_DEPS_REFRESHED=0"
        exit 1
    fi
    sudo "$PRIV" deps-marker --sha "$NEW_REQ_SHA"
    DEPS_REFRESHED=1
fi
echo "STP_DEPS_REFRESHED=$DEPS_REFRESHED"
APPLY_T1=$(date +%s%3N)
echo "STP_REMOTE_APPLY_MS=$((APPLY_T1 - APPLY_T0))"

# Restart service
RESTART_T0=$(date +%s%3N)
sudo "$PRIV" restart

# Verify service came back up（#1253 / R14-F07：WARN 不算成功——systemd 接受
# 重启但新进程立即崩溃时，必须让 API 得到 ok=False 而不是记录部署修订）。
SERVICE_ACTIVE=0
for i in 1 2 3 4 5; do
    sleep 1
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        SERVICE_ACTIVE=1
        break
    fi
done
if [ "$SERVICE_ACTIVE" -ne 1 ]; then
    systemctl --no-pager -l status "$SERVICE_NAME" | head -20 || true
    echo "ERROR: service $SERVICE_NAME not active 5s after restart; check: systemctl status $SERVICE_NAME"
    exit 1
fi
RESTART_T1=$(date +%s%3N)
echo "STP_RESTART_PROBE_MS=$((RESTART_T1 - RESTART_T0))"
echo "OK: service restarted successfully"

# ADR-0040 D2：收敛成功后受控写入 ARTIFACT_DIGEST（探活通过才写——中途失败
# 保持旧 digest，下一次收敛按 drift 重做）。与 VERSION 同通道、同信任模型。
if [ -n "$ARTIFACT_DIGEST" ]; then
    sudo "$PRIV" write-digest --digest "$ARTIFACT_DIGEST"
    echo "STP_ARTIFACT_DIGEST=$ARTIFACT_DIGEST"
fi

fi

# ── ADR-0040 P2-B（#1975）：resources 层独立收敛——D4：不重启、不触碰 deps/env。
# 空集守卫在控制面（plan_convergence：控制面 resources 分区为空永不下发本层）。
if [ -n "$RESOURCES_TARB_PATH" ]; then
RES_APPLY_T0=$(date +%s%3N)
# wrapper 子命令契约由脚本开头 selftest 保证（apply-resources/write-digest --kind
# 均在契约表内，旧 wrapper 会在 selftest 阶段 fail-closed）。
sudo "$PRIV" apply-resources --staged "$RES_TMP"
sudo "$PRIV" write-digest --kind resources --digest "$RESOURCES_DIGEST"
echo "STP_RESOURCES_DIGEST=$RESOURCES_DIGEST"
echo "STP_RESOURCES_APPLY_MS=$(( $(date +%s%3N) - RES_APPLY_T0 ))"
echo "STP_RESOURCES_APPLIED=1"
fi

"""


def _build_remote_script(
    *,
    install_dir: str,
    service_name: str,
    code_tar_path: str,
    resources_tar_path: str,
    user: str,
    group: str,
    sync_agent_secret: bool = False,
    agent_secret: str = "",
    pip_index_url: str = "",
    code_version: str = "",
    artifact_digest: str = "",
    resources_digest: str = "",
) -> str:
    agent_secret_b64 = ""
    if sync_agent_secret:
        agent_secret_b64 = base64.b64encode(agent_secret.encode("utf-8")).decode(
            "ascii"
        )

    env_overrides = hot_update_env_overrides(install_dir)
    env_overrides_b64 = base64.b64encode(
        json.dumps(env_overrides, sort_keys=True).encode("utf-8")
    ).decode("ascii")
    env_path_keys_b64 = base64.b64encode(
        json.dumps(agent_path_keys_to_verify(env_overrides)).encode("utf-8")
    ).decode("ascii")

    # #2180：user/group 已不被远端脚本使用（属主由 wrapper 内部固定），但保留在
    # 签名与调用方（安装器/测试契约稳定）；脚本模板里对应的占位符已删除，多余
    # 关键字参数对 str.format 无害。
    return _REMOTE_SCRIPT.format(
        install_dir=install_dir,
        service_name=service_name,
        code_tar_path=code_tar_path,
        resources_tar_path=resources_tar_path,
        sync_agent_secret="1" if sync_agent_secret else "0",
        agent_secret_b64=agent_secret_b64,
        env_overrides_b64=env_overrides_b64,
        env_path_keys_b64=env_path_keys_b64,
        user=user,
        group=group,
        pip_index_url=pip_index_url,
        code_version=code_version,
        artifact_digest=artifact_digest,
        resources_digest=resources_digest,
        # 能力集合注入（#2319）：与 _REQUIRED_PRIV_SUBCOMMANDS 同源
        required_priv_subcommands=" ".join(_REQUIRED_PRIV_SUBCOMMANDS),
    )


def _ssh_connect(host_ip: str, port: int, username: str,
                 password: str = "", key_path: str = "",
                 known_hosts_path: str = "",
                 timeout: int = 30):
    """Establish a paramiko SSH connection, returning (client, sftp)."""
    client = create_ssh_client(
        hostname=host_ip,
        port=port,
        username=username,
        password=password,
        key_path=key_path,
        known_hosts_path=known_hosts_path,
        timeout=timeout,
    )
    sftp = client.open_sftp()
    return client, sftp


def _remote_failure_message(stdout_text: str, stderr_text: str, exit_code: int) -> str:
    """远程脚本失败时的 message：优先取脚本/wrapper 打的 ``ERROR:`` 行。

    #1253：脚本 exit 1（如服务重启后 5s 仍未 active）时，API 的 message 必须
    携带原因，而不是一句无法定位的 "Remote script failed (exit=1)"。
    #1942：提权 wrapper 的拒绝（``STP_AGENT_PRIV_ERROR:``）与 argparse 用法错误
    只走 stderr——只扫 stdout 会让「旧 wrapper 缺子命令」退化成无因文案。
    顺序：stderr 哨兵 → stdout ``ERROR:`` 行 → stderr 末行（argparse 的
    ``error: argument ...`` 在末行）。
    """
    for line in stderr_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("STP_AGENT_PRIV_ERROR:"):
            return f"Remote script failed (exit={exit_code}): {stripped[:300]}"
    for line in stdout_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("ERROR:"):
            return f"Remote script failed (exit={exit_code}): {stripped[:300]}"
    for line in reversed(stderr_text.splitlines()):
        stripped = line.strip()
        if stripped:
            return f"Remote script failed (exit={exit_code}): {stripped[:300]}"
    return f"Remote script failed (exit={exit_code})"


def _parse_deps_refreshed(stdout_text: str) -> bool:
    """Extract the STP_DEPS_REFRESHED sentinel (0/1) from remote script stdout."""
    for line in reversed(stdout_text.splitlines()):
        line = line.strip()
        if line.startswith("STP_DEPS_REFRESHED="):
            return line.split("=", 1)[1].strip() == "1"
    return False


def _parse_priv_mode(stdout_text: str) -> str:
    """提权通道（#2180 后仅一态）：远端在 wrapper selftest 通过后打
    ``STP_PRIV_MODE=wrapper``；脚本更早失败（wrapper 缺失/旧版）时为 unknown。"""
    for line in stdout_text.splitlines():
        if line.strip() == "STP_PRIV_MODE=wrapper":
            return "wrapper"
    return "unknown"


def _parse_phase_ms(stdout_text: str, sentinel: str) -> int:
    """ADR-0040 D6：远端分段计时哨兵（毫秒）；缺省/不可解析回退 0。"""
    for line in reversed(stdout_text.splitlines()):
        line = line.strip()
        if line.startswith(sentinel + "="):
            raw = line.split("=", 1)[1].strip()
            try:
                return max(0, int(raw))
            except ValueError:
                return 0
    return 0


def _parse_env_synced(stdout_text: str) -> list[str]:
    """Extract allowlisted .env keys synced by the remote script."""
    for line in reversed(stdout_text.splitlines()):
        line = line.strip()
        if line.startswith("STP_ENV_SYNCED="):
            raw = line.split("=", 1)[1].strip()
            if not raw:
                return []
            return [key for key in raw.split(",") if key]
    return []


def _parse_env_paths_missing(stdout_text: str) -> dict[str, str]:
    """Extract synced .env keys whose value does not exist on the agent.

    The remote script base64-encodes the payload so paths containing the
    delimiter characters cannot garble the diagnostic.
    """
    for line in reversed(stdout_text.splitlines()):
        line = line.strip()
        if line.startswith("STP_ENV_PATH_MISSING="):
            raw = line.split("=", 1)[1].strip()
            if not raw:
                return {}
            try:
                decoded = json.loads(base64.b64decode(raw).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                logger.warning("hot_update_env_path_payload_unparsable raw=%s", raw[:120])
                return {}
            if not isinstance(decoded, dict):
                return {}
            return {str(k): str(v) for k, v in decoded.items()}
    return {}


def get_agent_code_version() -> str:
    """Return the short git HEAD of the agent source tree, or '' if unavailable."""
    import subprocess

    try:
        completed = subprocess.run(
            ["git", "-C", str(_AGENT_SOURCE_DIR), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if completed.returncode == 0:
            return completed.stdout.strip()
    except Exception:
        logger.debug("agent_code_version_lookup_failed", exc_info=True)
    return ""


def execute_hot_update(
    host_ip: str,
    ssh_port: int = 22,
    ssh_user: str = "root",
    ssh_password: str = "",
    ssh_key_path: str = "",
    known_hosts_path: str = "",
    install_user: str = "android",
    install_group: str = "android",
    sync_agent_secret: bool = False,
    agent_secret: str = "",
    code_version: str = "",
    pip_index_url: str = "",
    code_drift: bool = True,
    resources_drift: bool = False,
    code_tarball: bytes | None = None,
    resources_tarball: bytes | None = None,
    artifact_digest: str = "",
    resources_digest: str = "",
) -> dict:
    """Execute a layered hot-update on a remote Linux host（ADR-0040 §5-3 P2-B）。

    ``code_drift`` / ``resources_drift``：两层是否需要收敛（调用方
    ``plan_convergence`` 判定；force 时两层皆 True）。``code_tarball`` /
    ``resources_tarball``：可选预构建载荷（批量惰性构建复用）；None 且该层
    drift 时按需内建（UI/API 单台路径行为不变）。
    ``resources_tarball``：资源层载荷（resources/** 除 mtbf/）。None = 资源
    层无变更或空集守卫跳过；非 None 时远端独立收敛 resources/（**不重启**，
    D4），成功后写 ``ARTIFACT_DIGEST_RESOURCES``。
    ``artifact_digest`` / ``resources_digest``：两层 desired 身份，各层收敛
    成功后由远端受控写入（与 #1943 逐拍上报衔接）。

    Returns a dict with keys: ok, converged (bool), reason (str),
    artifact_digest (str), resources_digest (str), phases (dict), host_id
    (str), message, duration_ms, deps_refreshed (bool), env_keys_synced
    (list[str]), env_paths_missing (dict[str, str]), code_version (str),
    resources_applied (bool).
    Raises no exceptions — failures are captured in the returned dict.
    """
    import paramiko

    if not pip_index_url:
        pip_index_url = os.getenv("STP_AGENT_PIP_INDEX_URL", "")

    t0 = time.monotonic()
    phases: dict[str, int] = {"digest": 0}

    def _phase_ms(since: float) -> int:
        return int((time.monotonic() - since) * 1000)

    if not code_drift and not resources_drift:
        return {
            "ok": False,
            "converged": True,
            "reason": "nothing-to-converge",
            "message": "no code or resources layer requested",
            "duration_ms": 0,
            "deps_refreshed": False,
            "env_keys_synced": [],
            "env_paths_missing": {},
            "code_version": code_version,
            "priv_mode": "unknown",
            "artifact_digest": artifact_digest,
            "resources_digest": resources_digest,
            "resources_applied": False,
            "phases": {"digest": 0},
        }

    # #2285：失败归因按**阶段**分流（连接 / 上传 / 远端执行）——三者的 OSError 此前
    # 一律报成 ssh_connect_failed，把「目标机 /tmp 满、传输中断」误指成「查可达性与
    # SSH 端口」。stage 在 try 之前初始化，供外层 except 读取。
    stage = "connect"
    try:
        # 1. Build payloads（#1903：批量入口传预构建载荷整批复用；P2-B 分层
        #    惰性构建——每层首次需要时才构建，UI/API 单台按需内建）
        if code_drift and code_tarball is None:
            logger.info("hot_update_building_code_tarball source=%s", _AGENT_SOURCE_DIR)
            t_build = time.monotonic()
            code_tarball = _build_tarball(kind="code")
            phases["build_code"] = _phase_ms(t_build)
            logger.info("hot_update_code_tarball_size_bytes=%d", len(code_tarball))
        if resources_drift and resources_tarball is None:
            t_build = time.monotonic()
            resources_tarball = _build_resources_tarball()
            phases["build_resources"] = _phase_ms(t_build)
            logger.info(
                "hot_update_resources_tarball_size_bytes=%d", len(resources_tarball),
            )

        # 2. Connect
        t_connect = time.monotonic()
        client, sftp = _ssh_connect(
            host_ip=host_ip,
            port=ssh_port,
            username=ssh_user,
            password=ssh_password,
            key_path=ssh_key_path,
            known_hosts_path=known_hosts_path,
        )
        phases["connect"] = _phase_ms(t_connect)

        code_tar_path = _remote_tar_path() if code_drift else ""
        resources_tar_path = _remote_tar_path(prefix="res") if resources_drift else ""
        try:
            # 3. Upload payloads（按层上传）
            stage = "upload"
            t_upload = time.monotonic()
            if code_tarball is not None:
                logger.info("hot_update_uploading_code host=%s:%d", host_ip, ssh_port)
                sftp.putfo(io.BytesIO(code_tarball), code_tar_path)
                sftp.chmod(code_tar_path, 0o644)
            if resources_tarball is not None:
                logger.info(
                    "hot_update_uploading_resources host=%s:%d", host_ip, ssh_port,
                )
                sftp.putfo(io.BytesIO(resources_tarball), resources_tar_path)
                sftp.chmod(resources_tar_path, 0o644)
            phases["upload"] = _phase_ms(t_upload)

            # 4. Execute remote script（分层条件段：各层无 tar 即整段跳过）
            script = _build_remote_script(
                install_dir=_REMOTE_INSTALL_DIR,
                service_name=_REMOTE_SERVICE_NAME,
                code_tar_path=code_tar_path,
                resources_tar_path=resources_tar_path,
                user=install_user,
                group=install_group,
                sync_agent_secret=sync_agent_secret,
                agent_secret=agent_secret,
                pip_index_url=pip_index_url,
                code_version=code_version,
                artifact_digest=artifact_digest,
                resources_digest=resources_digest,
            )

            logger.info("hot_update_executing host=%s", host_ip)
            t_remote = time.monotonic()
            stage = "exec"
            stdin, stdout, stderr = client.exec_command(script)
            exit_code = stdout.channel.recv_exit_status()
            out_text = stdout.read().decode("utf-8", errors="replace")
            err_text = stderr.read().decode("utf-8", errors="replace")
            phases["remote_total"] = _phase_ms(t_remote)
            phases["remote_apply"] = _parse_phase_ms(out_text, "STP_REMOTE_APPLY_MS")
            phases["restart_probe"] = _parse_phase_ms(out_text, "STP_RESTART_PROBE_MS")

            deps_refreshed = _parse_deps_refreshed(out_text)
            env_keys_synced = _parse_env_synced(out_text)
            env_paths_missing = _parse_env_paths_missing(out_text)
            priv_mode = _parse_priv_mode(out_text)

            if exit_code != 0:
                logger.error("hot_update_remote_failed exit=%d stderr=%s", exit_code, err_text[:500])
                return {
                    "ok": False,
                    "converged": False,
                    "reason": "remote_script_failed",
                    "message": _remote_failure_message(out_text, err_text, exit_code),
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                    "deps_refreshed": deps_refreshed,
                    "env_keys_synced": env_keys_synced,
                    "env_paths_missing": env_paths_missing,
                    "code_version": code_version,
                    "priv_mode": priv_mode,
                    "artifact_digest": artifact_digest,
                    "phases": phases,
                }

            if env_paths_missing:
                logger.error(
                    "hot_update_env_paths_missing host=%s missing=%s",
                    host_ip, env_paths_missing,
                )

            msg = out_text.strip().split("\n")[-1] if out_text.strip() else "OK"
            logger.info(
                "hot_update_success host=%s msg=%s deps_refreshed=%s env_keys_synced=%s code_version=%s",
                host_ip, msg, deps_refreshed, env_keys_synced, code_version,
            )
            return {
                "ok": True,
                "converged": False,
                "reason": "deployed",
                "message": msg,
                "duration_ms": int((time.monotonic() - t0) * 1000),
                "deps_refreshed": deps_refreshed,
                "env_keys_synced": env_keys_synced,
                "env_paths_missing": env_paths_missing,
                "code_version": code_version,
                "priv_mode": priv_mode,
                "artifact_digest": artifact_digest,
                "phases": phases,
            }

        finally:
            # #960：用完即删 —— 每个包都是独立路径，留着只会堆积在 /tmp
            for stale in (code_tar_path, resources_tar_path):
                if stale:
                    try:
                        sftp.remove(stale)
                    except Exception:
                        pass
            sftp.close()
            client.close()

    except paramiko.AuthenticationException:
        msg = f"SSH authentication failed for {ssh_user}@{host_ip}"
        logger.warning("hot_update_auth_failed host=%s", host_ip)
        return {
            "ok": False,
            "converged": False,
            "reason": "ssh_auth_failed",
            "message": msg,
            "duration_ms": int((time.monotonic() - t0) * 1000),
            "deps_refreshed": False,
            "env_keys_synced": [],
            "env_paths_missing": {},
            "code_version": code_version,
            "priv_mode": "unknown",
            "artifact_digest": artifact_digest,
            "phases": phases,
        }

    except (OSError, IOError) as e:
        # code-scanning #80：异常原文只进日志，不外泄给 API 调用方（与下方
        # unexpected_error 兜底分支同一口径）——分类由稳定的 reason 承载，
        # 根因由日志锚点承载。
        # #2285：本 except 覆盖的 try 从建包起，HTTP/SFTP/exec 全在其中，paramiko
        # 把上传与会话失败也抛成 OSError —— 只报 ssh_connect_failed 会把「目标机
        # 盘满 / 传输中断」误指成「查可达性与 SSH 端口」。按 errno 优先、阶段其次分流。
        if getattr(e, "errno", None) in (errno.ENOSPC, errno.EDQUOT):
            reason, anchor = "remote_disk_full", "hot_update_remote_disk_full"
            msg = (
                f"The remote host {host_ip}:{ssh_port} reported no space (or exceeded "
                "its quota) while applying the update. Free space on the target "
                "filesystem (remote temp + install directory), then retry; the "
                "underlying error is in the control-plane log "
                "(hot_update_remote_disk_full)."
            )
        elif stage == "upload":
            reason, anchor = "remote_upload_failed", "hot_update_upload_failed"
            msg = (
                f"Uploading the update payload to {host_ip}:{ssh_port} failed (the SSH "
                "connection itself succeeded). Check the remote temp space and the "
                "install user's write permission, then retry; the underlying error "
                "is in the control-plane log (hot_update_upload_failed)."
            )
        elif stage == "exec":
            reason, anchor = "remote_exec_failed", "hot_update_exec_failed"
            msg = (
                f"The remote update script on {host_ip}:{ssh_port} could not be run "
                "(the SSH connection and the upload succeeded). Check the remote "
                "install directory and service state; the underlying error is in "
                "the control-plane log (hot_update_exec_failed)."
            )
        else:
            reason, anchor = "ssh_connect_failed", "hot_update_connection_failed"
            msg = (
                f"SSH connection to {host_ip}:{ssh_port} failed. Check host "
                "reachability and the SSH port, then retry; the underlying error "
                "is in the control-plane log (hot_update_connection_failed)."
            )
        logger.warning("%s host=%s:%d err=%s", anchor, host_ip, ssh_port, e)
        return {
            "ok": False,
            "converged": False,
            "reason": reason,
            "message": msg,
            "duration_ms": int((time.monotonic() - t0) * 1000),
            "deps_refreshed": False,
            "env_keys_synced": [],
            "env_paths_missing": {},
            "code_version": code_version,
            "priv_mode": "unknown",
            "artifact_digest": artifact_digest,
            "phases": phases,
        }

    except Exception:
        logger.exception("hot_update_unexpected_error host=%s", host_ip)
        return {
            "ok": False,
            "converged": False,
            "reason": "unexpected_error",
            "message": "Unexpected error during hot-update, check server logs.",
            "duration_ms": int((time.monotonic() - t0) * 1000),
            "deps_refreshed": False,
            "env_keys_synced": [],
            "env_paths_missing": {},
            "code_version": code_version,
            "priv_mode": "unknown",
            "artifact_digest": artifact_digest,
            "phases": phases,
        }
