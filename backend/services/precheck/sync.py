"""Script sync to agent hosts (lightweight SFTP + hot-update fallback)."""

from __future__ import annotations

import logging
import os
from typing import Optional

from sqlalchemy.orm import Session

from backend.core.ssh_security import (
    SshSecurityConfigError,
    create_ssh_client,
    resolve_host_ssh_credentials,
)
from backend.models.host import Host
from backend.services.host_updater import (
    _AGENT_SOURCE_DIR,
    _resolve_ssh_creds,
    execute_hot_update,
)

from . import _REMOTE_AGENT_PREFIX

logger = logging.getLogger(__name__)


def nfs_path_to_local(nfs_path: str) -> str | None:
    if not nfs_path.startswith(_REMOTE_AGENT_PREFIX):
        return None
    rel = nfs_path[len(_REMOTE_AGENT_PREFIX):]
    return str(_AGENT_SOURCE_DIR / rel)


def _get_ssh_client(
    host_ip: str,
    host_ssh_port: int,
    ssh_user: str,
    ssh_password: str,
    ssh_key_path: str,
    known_hosts_path: str = "",
):
    return create_ssh_client(
        hostname=host_ip,
        port=host_ssh_port,
        username=ssh_user,
        password=ssh_password,
        key_path=ssh_key_path,
        known_hosts_path=known_hosts_path,
        timeout=15,
    )


def sync_host_via_hot_update(host_id: str, db: Session) -> tuple[bool, Optional[str]]:
    host = db.get(Host, host_id)
    if host is None:
        return False, "host_not_found"
    if not host.ip:
        return False, "host_missing_ip"

    try:
        creds, _migrated = resolve_host_ssh_credentials(
            host, inventory_lookup=_resolve_ssh_creds,
        )
    except SshSecurityConfigError as exc:
        return False, f"ssh_security_config_error: {exc}"
    if not creds.password and not creds.key_path:
        return False, "no_ssh_credentials"

    try:
        result = execute_hot_update(
            host_ip=host.ip,
            ssh_port=host.ssh_port or 22,
            ssh_user=creds.user,
            ssh_password=creds.password,
            ssh_key_path=creds.key_path,
            known_hosts_path=creds.known_hosts_path,
        )
    except Exception as exc:
        return False, f"hot_update_exception: {exc}"

    if not result.get("ok"):
        return False, f"hot_update_failed: {result.get('message', 'unknown')}"
    return True, None


def push_mismatched_scripts(
    host_id: str, mismatched: list[dict], db: Session,
) -> tuple[bool, str | None]:
    host = db.get(Host, host_id)
    if host is None:
        return False, "host_not_found"
    if not host.ip:
        return False, "host_missing_ip"

    try:
        creds, _migrated = resolve_host_ssh_credentials(
            host, inventory_lookup=_resolve_ssh_creds,
        )
    except SshSecurityConfigError as exc:
        return False, f"ssh_security_config_error: {exc}"
    if not creds.password and not creds.key_path:
        return False, "no_ssh_credentials"

    pushed = 0
    failed: list[str] = []
    client = None
    try:
        client = _get_ssh_client(
            host.ip, host.ssh_port or 22, creds.user,
            creds.password, creds.key_path, creds.known_hosts_path,
        )
        sftp = client.open_sftp()

        for script in mismatched:
            nfs_path = script.get("nfs_path", "")
            local_path = nfs_path_to_local(nfs_path)
            if not local_path:
                failed.append(f"{script['name']}: cannot map nfs_path")
                continue

            if not os.path.isfile(local_path):
                failed.append(f"{script['name']}: local file not found: {local_path}")
                continue

            remote_dir = os.path.dirname(nfs_path)
            try:
                sftp.stat(remote_dir)
            except FileNotFoundError:
                parts = remote_dir.lstrip("/").split("/")
                path = ""
                for p in parts:
                    path = f"{path}/{p}"
                    try:
                        sftp.stat(path)
                    except FileNotFoundError:
                        sftp.mkdir(path)

            try:
                sftp.put(local_path, nfs_path)
                pushed += 1
            except Exception as exc:
                failed.append(f"{script['name']}: SFTP failed: {exc}")

            local_dir = os.path.dirname(local_path)
            adb_helper = os.path.join(local_dir, "_adb.py")
            if os.path.isfile(adb_helper):
                adb_remote = os.path.join(remote_dir, "_adb.py")
                try:
                    sftp.put(adb_helper, adb_remote)
                except Exception:
                    pass

        sftp.close()

        if pushed > 0:
            cmd_parts = []
            for script in mismatched:
                nfs = script.get("nfs_path", "")
                if nfs:
                    cmd_parts.append(f"sed -i 's/\\r$//' '{nfs}' 2>/dev/null || true")
                    cmd_parts.append(f"chmod 755 '{nfs}' 2>/dev/null || true")
            if cmd_parts:
                client.exec_command("; ".join(cmd_parts))

    except Exception as exc:
        return False, f"ssh_exception: {exc}"
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass

    if failed:
        return False, f"partial_fail: pushed={pushed}, failed={'; '.join(failed)}"

    logger.info(
        "lightweight_sync_done host=%s pushed=%d scripts=%s",
        host_id, pushed, [s["name"] for s in mismatched],
    )
    return True, None


_sync_host_via_hot_update = sync_host_via_hot_update
_push_mismatched_scripts = push_mismatched_scripts
