"""Hot-update agent code on a remote Linux host via SSH + rsync.

Uses paramiko (already a project dependency) to:
1. Package the local agent source tree into a tar.gz
2. SFTP it to the remote host's /tmp
3. SSH-exec a remote script that extracts, rsyncs to the install dir,
   and restarts the systemd service.
"""

from __future__ import annotations

import base64
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
_TAR_EXCLUDES = {
    "__pycache__",
    "tests",
    ".env.example",
    "install_agent.sh",
    "agentctl.sh",
    "DEPLOY.md",
    "stability-test-agent.service",
    "hosts.txt",
}

# File suffixes to exclude
_TAR_EXCLUDE_SUFFIXES = (".pyc",)

# ADR-0040 §5.1（P0 过渡，#1903）：压缩级 9 → 6。实测 252MB 源树打包 16.6s → 6.4s，
# 体积 125.7MB → 126.0MB（+0.3MB，内网传输代价可忽略）。终态出口 = P1 的 digest 缓存键，
# 不留双轨。
_TARBALL_COMPRESSLEVEL = 6

# ADR-0040 D1：部署载荷不含「主机态/部署态」文件——远端布局里 agent/ 下可能
# 存在的元数据（VERSION/ARTIFACT_DIGEST/.env）不进身份；resources/mtbf/ 永远
# 属主机本地（#214/#216 APK 保护语义），tarball 也不应携带（远端 rsync --delete
# 本就排除，不进包让「载荷 == 安装树」更真）。
_PAYLOAD_METADATA_EXCLUDES = {"VERSION", "ARTIFACT_DIGEST", ".env"}


def _iter_payload_files(kind: str = "full"):
    """Yield ``(abs_path, arcname)`` over the deploy payload file set.

    tarball（``_build_tarball``）与 artifact digest（``artifact_digest.collect_artifact_entries``）
    共享同一枚举——digest 输入集 = 部署输入集由同一份代码保证（ADR-0040 D1）。
    symlink 一律跳过：tar 存链接本身而内容读取会穿透，两侧身份会分叉。

    kind 分层（ADR-0040 §5-3 P2-B，#1975）：``full`` = P1 全集（兼容语义保留）；
    ``code`` = 代码树 + schema（**不含 resources/**，分层后 ~1MB）；``resources``
    = ``resources/**``（除 ``resources/mtbf/``——永远属主机本地）。code 与
    resources 互斥、并集 == full − mtbf（契约测试守护）。
    """
    for root, dirs, files in os.walk(_AGENT_SOURCE_DIR):
        # Filter directories in-place
        dirs[:] = [d for d in dirs if d not in _TAR_EXCLUDES]

        for name in files:
            if name in _TAR_EXCLUDES:
                continue
            if name.endswith(_TAR_EXCLUDE_SUFFIXES):
                continue
            if name.startswith("test_") and name.endswith(".py"):
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
    compresslevel: int = _TARBALL_COMPRESSLEVEL, kind: str = "code"
) -> bytes:
    """Package the deploy payload (``kind``: code / resources / full) into a tarball."""
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

# 提权边界（#1250/ADR-0037）：优先走 stp-agent-priv wrapper；未迁移主机
# （wrapper 或 conf 缺失）回退旧 sudo 面并留哨兵，便于控制面观测迁移进度。
PRIV="/usr/local/sbin/stp-agent-priv"
USE_PRIV_WRAPPER=0
if sudo -n "$PRIV" selftest >/dev/null 2>&1; then
    USE_PRIV_WRAPPER=1
    echo "STP_PRIV_MODE=wrapper"
else
    echo "STP_PRIV_FALLBACK=legacy"
fi

# #1942：wrapper 能力协商。热更新载荷不含 wrapper（ADR-0037——wrapper 在安装目录
# 外、root 所有，由 install/Ansible 轨道交付），存量主机可能带着缺后加子命令的旧
# wrapper：若不在动作前探测，失败会发生在「代码已同步、服务已重启」之后（尾部
# exit 2），既留「已部署却记失败」的半态，又让下一次批量重复全量部署。
# `write-digest --digest ""` 是空操作探针（支持时打 STP_WRITE_DIGEST_SKIPPED 并
# exit 0；旧 wrapper 走 argparse 拒绝 exit 2），探针本身不落任何文件。
if [ -n "$CODE_TARB_PATH" ]; then
if [ "$USE_PRIV_WRAPPER" = "1" ] && [ -n "$ARTIFACT_DIGEST" ]; then
    if ! sudo -n "$PRIV" write-digest --digest "" >/dev/null 2>&1; then
        echo "ERROR: stp-agent-priv lacks write-digest (outdated wrapper); run tools/ansible/playbooks/update_agent.yml on this host, then retry"
        exit 1
    fi
fi

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
if [ "$USE_PRIV_WRAPPER" = "1" ]; then
    # wrapper：固定目标 + 固定 excludes（含 mtbf protect）+ --safe-links
    sudo "$PRIV" apply-code --staged "$CODE_TMP"
else
    sudo rsync -av --delete \
        --exclude='__pycache__/' \
        --exclude='tests/' \
        --exclude='resources/mtbf/' \
        --exclude='.env.example' \
        --exclude='install_agent.sh' \
        --exclude='agentctl.sh' \
        --exclude='DEPLOY.md' \
        --exclude='stability-test-agent.service' \
        --exclude='hosts.txt' \
        --filter='protect resources/' \
        "$CODE_TMP/" "$INSTALL_DIR/agent/"
fi

CODE_VERSION="{code_version}"
if [ -n "$CODE_VERSION" ]; then
    if [ "$USE_PRIV_WRAPPER" = "1" ]; then
        sudo "$PRIV" write-version --version "$CODE_VERSION"
    else
        echo "$CODE_VERSION" | sudo tee "$INSTALL_DIR/agent/VERSION" > /dev/null
    fi
fi

if [ -f "$CODE_TMP/stp_schemas/pipeline_schema.json" ]; then
    if [ "$USE_PRIV_WRAPPER" = "1" ]; then
        sudo "$PRIV" install-schema --file "$CODE_TMP/stp_schemas/pipeline_schema.json"
    else
        sudo mkdir -p "$INSTALL_DIR/schemas"
        sudo install -m 0644 "$CODE_TMP/stp_schemas/pipeline_schema.json" "$INSTALL_DIR/schemas/pipeline_schema.json"
    fi
fi

if [ "$SYNC_AGENT_SECRET" = "1" ]; then
    if [ "$USE_PRIV_WRAPPER" = "1" ]; then
        sudo "$PRIV" sync-env --secret-b64 "$AGENT_SECRET_B64"
    else
    sudo INSTALL_DIR="$INSTALL_DIR" AGENT_SECRET_B64="$AGENT_SECRET_B64" python3 - <<'PY'
import base64
import os
import pathlib
import sys

env_path = pathlib.Path(os.environ["INSTALL_DIR"]) / ".env"
if not env_path.exists():
    print("ERROR: Agent env file missing at " + str(env_path), file=sys.stderr)
    raise SystemExit(1)

secret = base64.b64decode(os.environ["AGENT_SECRET_B64"]).decode("utf-8")
lines = env_path.read_text(encoding="utf-8").splitlines()
updated_lines = []
replaced = False

for line in lines:
    if line.startswith("AGENT_SECRET="):
        updated_lines.append("AGENT_SECRET=" + secret)
        replaced = True
    else:
        updated_lines.append(line)

if not replaced:
    updated_lines.append("AGENT_SECRET=" + secret)

env_path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
PY
    fi
fi

if [ "$USE_PRIV_WRAPPER" = "1" ]; then
    sudo "$PRIV" sync-env --overrides-b64 "$ENV_OVERRIDES_B64" --path-keys-b64 "$ENV_PATH_KEYS_B64"
else
sudo INSTALL_DIR="$INSTALL_DIR" ENV_OVERRIDES_B64="$ENV_OVERRIDES_B64" ENV_PATH_KEYS_B64="$ENV_PATH_KEYS_B64" python3 - <<'PY'
import base64
import json
import os
import pathlib
import sys

env_path = pathlib.Path(os.environ["INSTALL_DIR"]) / ".env"
overrides = json.loads(base64.b64decode(os.environ["ENV_OVERRIDES_B64"]).decode("utf-8"))
path_keys = json.loads(base64.b64decode(os.environ["ENV_PATH_KEYS_B64"]).decode("utf-8"))
if not overrides:
    print("STP_ENV_SYNCED=")
    print("STP_ENV_PATH_MISSING=")
    raise SystemExit(0)

if not env_path.exists():
    print("ERROR: Agent env file missing at " + str(env_path), file=sys.stderr)
    raise SystemExit(1)

lines = env_path.read_text(encoding="utf-8").splitlines()
seen = set()
updated_keys = []
new_lines = []

for line in lines:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        new_lines.append(line)
        continue
    key, _, _ = line.partition("=")
    key = key.strip()
    if key in overrides:
        new_lines.append(f"{{key}}={{overrides[key]}}")
        seen.add(key)
        updated_keys.append(key)
    else:
        new_lines.append(line)

for key, val in overrides.items():
    if key not in seen:
        new_lines.append(f"{{key}}={{val}}")
        updated_keys.append(key)

env_path.write_text("\n".join(new_lines) + ("\n" if new_lines else ""), encoding="utf-8")
print("STP_ENV_SYNCED=" + ",".join(sorted(updated_keys)))

missing = {{
    key: overrides[key]
    for key in sorted(path_keys)
    if key in overrides and not os.path.exists(overrides[key])
}}
print("STP_ENV_PATH_MISSING=" + base64.b64encode(
    json.dumps(missing, sort_keys=True).encode("utf-8")
).decode("ascii"))
PY
fi

# Fix ownership
if [ "$USE_PRIV_WRAPPER" = "1" ]; then
    sudo "$PRIV" fix-ownership
else
    sudo chown -R {user}:{group} "$INSTALL_DIR"
fi

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
    if [ "$USE_PRIV_WRAPPER" = "1" ]; then
        sudo "$PRIV" deps-marker --sha "$NEW_REQ_SHA"
    else
        echo "$NEW_REQ_SHA" | sudo tee "$DEPS_MARKER" > /dev/null
        sudo chown {user}:{group} "$DEPS_MARKER"
    fi
    DEPS_REFRESHED=1
fi
echo "STP_DEPS_REFRESHED=$DEPS_REFRESHED"
APPLY_T1=$(date +%s%3N)
echo "STP_REMOTE_APPLY_MS=$((APPLY_T1 - APPLY_T0))"

# Restart service
RESTART_T0=$(date +%s%3N)
if [ "$USE_PRIV_WRAPPER" = "1" ]; then
    sudo "$PRIV" restart
else
    sudo systemctl restart "$SERVICE_NAME"
fi

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
    if [ "$USE_PRIV_WRAPPER" = "1" ]; then
        sudo "$PRIV" write-digest --digest "$ARTIFACT_DIGEST"
    else
        printf '%s\n' "$ARTIFACT_DIGEST" | sudo tee "$INSTALL_DIR/agent/ARTIFACT_DIGEST" > /dev/null
    fi
    echo "STP_ARTIFACT_DIGEST=$ARTIFACT_DIGEST"
fi

fi

# ── ADR-0040 P2-B（#1975）：resources 层独立收敛——D4：不重启、不触碰 deps/env。
# 空集守卫在控制面（plan_convergence：控制面 resources 分区为空永不下发本层）。
if [ -n "$RESOURCES_TARB_PATH" ]; then
RES_APPLY_T0=$(date +%s%3N)
USE_RES_WRAPPER=$USE_PRIV_WRAPPER
if [ "$USE_PRIV_WRAPPER" = "1" ]; then
    # apply-resources 能力协商（#1942 同模式）：旧 wrapper 缺子命令 → legacy
    # rsync 回退（degrade 安全方向：resources 不更新、digest 不写，下轮再收敛）。
    if ! sudo -n "$PRIV" apply-resources --help >/dev/null 2>&1; then
        USE_RES_WRAPPER=0
        echo "STP_RESOURCES_PRIV_FALLBACK=legacy"
    fi
fi
if [ "$USE_RES_WRAPPER" = "1" ]; then
    sudo "$PRIV" apply-resources --staged "$RES_TMP"
else
    sudo rsync -a --no-owner --no-group --delete --safe-links \
        --exclude='mtbf/' \
        --filter='protect mtbf/' \
        "$RES_TMP/resources/" "$INSTALL_DIR/agent/resources/"
fi
# resources 身份在收敛成功后写入（write-digest --kind resources；旧 wrapper
# 缺 --kind → WARN 跳过，P2-B 对该主机多一次全量 resources 部署，安全方向）。
if [ "$USE_RES_WRAPPER" = "1" ]; then
    if sudo -n "$PRIV" write-digest --digest "" --kind resources >/dev/null 2>&1; then
        sudo "$PRIV" write-digest --kind resources --digest "$RESOURCES_DIGEST"
        echo "STP_RESOURCES_DIGEST=$RESOURCES_DIGEST"
    else
        # 旧 wrapper 缺 --kind：WARN 跳过（不走绕过提权边界的裸写）
        echo "WARN: resources digest not written (outdated wrapper)"
    fi
else
    printf '%s\n' "$RESOURCES_DIGEST" | sudo tee "$INSTALL_DIR/agent/ARTIFACT_DIGEST_RESOURCES" > /dev/null
    echo "STP_RESOURCES_DIGEST=$RESOURCES_DIGEST"
fi
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
    """提权通道：#1250 迁移期 wrapper 与 legacy 并存，远端留模式哨兵。"""
    for line in stdout_text.splitlines():
        line = line.strip()
        if line == "STP_PRIV_MODE=wrapper":
            return "wrapper"
        if line == "STP_PRIV_FALLBACK=legacy":
            return "legacy"
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
        msg = f"SSH connection failed: {e}"
        logger.warning("hot_update_connection_failed host=%s:%d err=%s", host_ip, ssh_port, e)
        return {
            "ok": False,
            "converged": False,
            "reason": "ssh_connect_failed",
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
