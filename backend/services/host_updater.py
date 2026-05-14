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
import logging
import os
import tarfile
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Paths
_AGENT_SOURCE_DIR = Path(__file__).resolve().parent.parent / "agent"
_REMOTE_INSTALL_DIR = "/opt/stability-test-agent"
_REMOTE_SERVICE_NAME = "stability-test-agent"
_REMOTE_TAR_PATH = "/tmp/stp-agent-update.tar.gz"

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


def _resolve_ssh_creds(host_ip: str) -> dict | None:
    """Look up SSH credentials from Ansible inventory by IP.

    Returns dict with keys: user, password, port, or None if not found.
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
                    "port": int(parts.get("ansible_port", "22")),
                }
    except Exception:
        logger.warning("inventory_parse_failed", exc_info=True)

    return None


def _build_tarball() -> bytes:
    """Package the agent source tree into an in-memory gzipped tarball."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
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
                arcname = os.path.relpath(full_path, _AGENT_SOURCE_DIR)
                tar.add(full_path, arcname=arcname)

    return buf.getvalue()


_REMOTE_SCRIPT = r"""#!/bin/bash
set -e
INSTALL_DIR="{install_dir}"
SERVICE_NAME="{service_name}"
TAR_PATH="{tar_path}"
SYNC_AGENT_SECRET="{sync_agent_secret}"
AGENT_SECRET_B64="{agent_secret_b64}"

if [ ! -d "$INSTALL_DIR" ]; then
    echo "ERROR: Agent not installed at $INSTALL_DIR"
    rm -f "$TAR_PATH"
    exit 1
fi

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR" "$TAR_PATH"' EXIT

tar xzf "$TAR_PATH" -C "$TMPDIR"

# Fix CRLF from Windows sources
find "$TMPDIR" -type f \( -name "*.py" -o -name "*.sh" \) \
    -exec sed -i 's/\r$//' {{}} + 2>/dev/null || true

# Rsync into install dir
sudo rsync -av --delete \
    --exclude='__pycache__/' \
    --exclude='tests/' \
    --exclude='.env.example' \
    --exclude='install_agent.sh' \
    --exclude='agentctl.sh' \
    --exclude='DEPLOY.md' \
    --exclude='stability-test-agent.service' \
    --exclude='hosts.txt' \
    "$TMPDIR/" "$INSTALL_DIR/agent/"

if [ "$SYNC_AGENT_SECRET" = "1" ]; then
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

# Fix ownership
sudo chown -R {user}:{group} "$INSTALL_DIR"

# Restart service
sudo systemctl restart "$SERVICE_NAME"

# Verify service came back up
sleep 2
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "OK: service restarted successfully"
else
    echo "WARN: service may not be running, check: systemctl status $SERVICE_NAME"
fi
"""


def _build_remote_script(
    *,
    install_dir: str,
    service_name: str,
    tar_path: str,
    user: str,
    group: str,
    sync_agent_secret: bool = False,
    agent_secret: str = "",
) -> str:
    agent_secret_b64 = ""
    if sync_agent_secret:
        agent_secret_b64 = base64.b64encode(agent_secret.encode("utf-8")).decode(
            "ascii"
        )

    return _REMOTE_SCRIPT.format(
        install_dir=install_dir,
        service_name=service_name,
        tar_path=tar_path,
        sync_agent_secret="1" if sync_agent_secret else "0",
        agent_secret_b64=agent_secret_b64,
        user=user,
        group=group,
    )


def _ssh_connect(host_ip: str, port: int, username: str,
                 password: str = "", key_path: str = "",
                 timeout: int = 30):
    """Establish a paramiko SSH connection, returning (client, sftp)."""
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs = {
        "hostname": host_ip,
        "port": port,
        "username": username,
        "timeout": timeout,
    }

    if key_path and os.path.exists(key_path):
        connect_kwargs["key_filename"] = key_path
    elif password:
        connect_kwargs["password"] = password
    else:
        # Let paramiko try default keys
        pass

    client.connect(**connect_kwargs)
    sftp = client.open_sftp()
    return client, sftp


def execute_hot_update(
    host_ip: str,
    ssh_port: int = 22,
    ssh_user: str = "root",
    ssh_password: str = "",
    ssh_key_path: str = "",
    install_user: str = "android",
    install_group: str = "android",
    sync_agent_secret: bool = False,
    agent_secret: str = "",
) -> dict:
    """Execute a hot-update on a remote Linux host.

    Returns a dict with keys: ok, host_id (str), message, duration_ms.
    Raises no exceptions — failures are captured in the returned dict.
    """
    import paramiko

    t0 = time.monotonic()

    try:
        # 1. Build tarball
        logger.info("hot_update_building_tarball source=%s", _AGENT_SOURCE_DIR)
        tarball = _build_tarball()
        logger.info("hot_update_tarball_size_bytes=%d", len(tarball))

        # 2. Connect
        client, sftp = _ssh_connect(
            host_ip=host_ip,
            port=ssh_port,
            username=ssh_user,
            password=ssh_password,
            key_path=ssh_key_path,
        )

        try:
            # 3. Upload tarball
            logger.info("hot_update_uploading host=%s:%d", host_ip, ssh_port)
            sftp.putfo(io.BytesIO(tarball), _REMOTE_TAR_PATH)
            sftp.chmod(_REMOTE_TAR_PATH, 0o644)

            # 4. Execute remote script
            script = _build_remote_script(
                install_dir=_REMOTE_INSTALL_DIR,
                service_name=_REMOTE_SERVICE_NAME,
                tar_path=_REMOTE_TAR_PATH,
                user=install_user,
                group=install_group,
                sync_agent_secret=sync_agent_secret,
                agent_secret=agent_secret,
            )

            logger.info("hot_update_executing host=%s", host_ip)
            stdin, stdout, stderr = client.exec_command(script)
            exit_code = stdout.channel.recv_exit_status()
            out_text = stdout.read().decode("utf-8", errors="replace")
            err_text = stderr.read().decode("utf-8", errors="replace")

            if exit_code != 0:
                logger.error("hot_update_remote_failed exit=%d stderr=%s", exit_code, err_text[:500])
                return {
                    "ok": False,
                    "message": f"Remote script failed (exit={exit_code}): {err_text[:300]}",
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                }

            msg = out_text.strip().split("\n")[-1] if out_text.strip() else "OK"
            logger.info("hot_update_success host=%s msg=%s", host_ip, msg)
            return {
                "ok": True,
                "message": msg,
                "duration_ms": int((time.monotonic() - t0) * 1000),
            }

        finally:
            sftp.close()
            client.close()

    except paramiko.AuthenticationException:
        msg = f"SSH authentication failed for {ssh_user}@{host_ip}"
        logger.warning("hot_update_auth_failed host=%s", host_ip)
        return {"ok": False, "message": msg, "duration_ms": int((time.monotonic() - t0) * 1000)}

    except (OSError, IOError) as e:
        msg = f"SSH connection failed: {e}"
        logger.warning("hot_update_connection_failed host=%s:%d err=%s", host_ip, ssh_port, e)
        return {"ok": False, "message": msg, "duration_ms": int((time.monotonic() - t0) * 1000)}

    except Exception:
        logger.exception("hot_update_unexpected_error host=%s", host_ip)
        return {
            "ok": False,
            "message": "Unexpected error during hot-update, check server logs.",
            "duration_ms": int((time.monotonic() - t0) * 1000),
        }
