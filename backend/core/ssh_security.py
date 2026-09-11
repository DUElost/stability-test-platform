"""SSH credential, host-key, and log-path hardening helpers."""

from __future__ import annotations

import fcntl
import os
import posixpath
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from cryptography.fernet import Fernet, InvalidToken
import paramiko

DEFAULT_SSH_LOG_ROOTS = "/opt/stability-test-agent/logs,/var/log"
LOG_PATH_FORBIDDEN_MARKER = "STP_LOG_PATH_FORBIDDEN"
LOG_FILE_NOT_FOUND_MARKER = "STP_LOG_FILE_NOT_FOUND"
_TEST_FERNET_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


class SshSecurityConfigError(RuntimeError):
    """Raised when SSH security prerequisites are not configured."""


@dataclass
class ResolvedSshCredentials:
    user: str
    password: str = ""
    key_path: str = ""
    known_hosts_path: str = ""


def encrypt_ssh_password(password: str) -> str:
    secret = password or ""
    if secret == "":
        return ""
    return _get_fernet().encrypt(secret.encode("utf-8")).decode("utf-8")


def decrypt_ssh_password(ciphertext: str) -> str:
    token = (ciphertext or "").strip()
    if not token:
        return ""
    try:
        return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise SshSecurityConfigError("encrypted SSH password cannot be decrypted") from exc


def resolve_host_ssh_credentials(
    host,
    *,
    inventory_lookup: Callable[[str], dict | None] | None = None,
) -> tuple[ResolvedSshCredentials, bool]:
    extra = dict(getattr(host, "extra", None) or {})
    migrated = False

    legacy_key_path = str(extra.pop("ssh_key_path", "") or "").strip()
    if legacy_key_path and not getattr(host, "ssh_key_path", None):
        host.ssh_key_path = legacy_key_path
        migrated = True

    legacy_password = str(extra.pop("ssh_password", "") or "")
    if legacy_password and not getattr(host, "ssh_password_enc", None):
        host.ssh_password_enc = encrypt_ssh_password(legacy_password)
        migrated = True

    if extra != (getattr(host, "extra", None) or {}):
        host.extra = extra
        migrated = True

    password = decrypt_ssh_password(getattr(host, "ssh_password_enc", None) or "")
    key_path = (getattr(host, "ssh_key_path", None) or "").strip()
    user = (getattr(host, "ssh_user", None) or "root").strip() or "root"

    if not password and not key_path and inventory_lookup is not None:
        host_ip = getattr(host, "ip", None) or getattr(host, "ip_address", None) or ""
        inventory_creds = inventory_lookup(host_ip) if host_ip else None
        if inventory_creds:
            user = (inventory_creds.get("user") or user).strip() or user
            password = inventory_creds.get("password") or ""
            key_path = (
                inventory_creds.get("key_path")
                or inventory_creds.get("private_key")
                or inventory_creds.get("ssh_private_key")
                or ""
            ).strip()

    known_hosts_path = (
        getattr(host, "ssh_known_hosts_path", None)
        or os.getenv("STP_SSH_KNOWN_HOSTS", "")
    ).strip()

    return ResolvedSshCredentials(
        user=user,
        password=password,
        key_path=key_path,
        known_hosts_path=known_hosts_path,
    ), migrated


def get_ssh_log_roots() -> tuple[str, ...]:
    raw = os.getenv("STP_SSH_LOG_ROOTS", DEFAULT_SSH_LOG_ROOTS)
    roots = tuple(_normalize_posix_root(item) for item in raw.split(",") if item.strip())
    if not roots:
        raise SshSecurityConfigError("STP_SSH_LOG_ROOTS must contain at least one absolute path")
    return roots


def _resolve_known_hosts_path(explicit: str = "") -> Path:
    """Resolve the known_hosts file to use for host-key trust writes.

    Order: explicit arg > STP_SSH_KNOWN_HOSTS env > ~/.ssh/known_hosts.
    """
    raw = (explicit or os.getenv("STP_SSH_KNOWN_HOSTS", "") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".ssh" / "known_hosts"


def host_key_fingerprints(known_host_lines: list[str]) -> list[str]:
    """known_hosts 行的 SHA256 指纹列表（``SHA256:<base64>``，ssh-keygen 风格）。

    #908：换钥审计与拒绝原因需要可比对的稳定指纹（行内含网络地址，不能直接
    进审计文本对比）。
    """
    import base64
    import hashlib

    fingerprints: list[str] = []
    for line in known_host_lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            blob = base64.b64decode(parts[2], validate=True)
        except Exception:  # noqa: BLE001 - 非法行不阻塞指纹提取
            continue
        digest = hashlib.sha256(blob).digest()
        fingerprints.append(
            "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")
        )
    return fingerprints


def trust_host_key(
    ip: str,
    port: int = 22,
    known_hosts_path: str = "",
    timeout: int = 10,
    *,
    allow_replace: bool = False,
) -> tuple[bool, str]:
    """Best-effort ssh-keyscan of (ip, port) appended to known_hosts.

    Returns (ok, reason). Never raises — failures are captured so callers can
    surface a warning without blocking host creation.

    #908（R02-R02）：**已存在不同主机密钥时不静默覆盖**——默认拒绝并返回
    ``host key changed``（含新旧指纹）；调用方（管理员）显式传
    ``allow_replace=True`` 才替换，返回 ``replaced old=<fp> new=<fp>`` 供审计。
    首次信任（无既有条目）与同键重扫不受影响。
    """
    ip = (ip or "").strip()
    if not ip:
        return False, "empty ip"

    try:
        path = _resolve_known_hosts_path(known_hosts_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)

        # Probe the host key. ssh-keyscan prints plain `ip` for port 22 and
        # `[ip]:port` for non-default ports.
        keyscan = subprocess.run(
            ["ssh-keyscan", "-T", str(timeout), "-p", str(port), ip],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
            check=False,
        )
        new_keys = [ln for ln in keyscan.stdout.splitlines() if ln.strip() and not ln.startswith("#")]
        if not new_keys:
            return False, f"ssh-keyscan returned no keys (rc={keyscan.returncode})"

        # Rewrite the file under an exclusive lock: replace prior entries for
        # this host (or refuse when the key changed without explicit consent).
        port_token = f"[{ip}]:{port}" if port and port != 22 else None

        def _host_token(line: str) -> str:
            return line.split(" ", 1)[0] if line.strip() else ""

        result_reason = "ok"
        with open(path, "r+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                existing = fh.read().splitlines()
                prior_for_host = [
                    ln for ln in existing
                    if _host_token(ln) == ip
                    or (port_token is not None and _host_token(ln) == port_token)
                ]
                if prior_for_host and set(prior_for_host) != set(new_keys):
                    old_fp = ", ".join(host_key_fingerprints(prior_for_host))
                    new_fp = ", ".join(host_key_fingerprints(new_keys))
                    if not allow_replace:
                        # 保持原条目不动 —— 不做无法审计的信任替换
                        return False, (
                            f"host key changed for {ip}:{port} "
                            f"(existing {old_fp} -> scanned {new_fp}); "
                            "explicit replace required"
                        )
                    result_reason = f"replaced old={old_fp} new={new_fp}"

                kept = [
                    ln for ln in existing
                    if _host_token(ln) != ip
                    and (port_token is None or _host_token(ln) != port_token)
                ]
                merged = kept + new_keys
                fh.seek(0)
                fh.truncate()
                fh.write("\n".join(merged) + "\n")
                fh.flush()
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
        return True, result_reason
    except Exception as exc:  # noqa: BLE001 — best-effort, never raise
        return False, f"{type(exc).__name__}: {exc}"


def normalize_remote_log_path(
    log_path: str,
    *,
    allowed_roots: tuple[str, ...] | None = None,
) -> str:
    raw = (log_path or "").strip()
    if not raw:
        raise ValueError("log_path is required")
    if "\x00" in raw:
        raise ValueError("log_path contains NUL byte")
    if not raw.startswith("/"):
        raise ValueError("log_path must be an absolute Linux path")
    if ".." in PurePosixPath(raw).parts:
        raise ValueError("log_path must not contain '..'")

    normalized = posixpath.normpath(raw)
    roots = allowed_roots or get_ssh_log_roots()
    if not any(posixpath.commonpath([normalized, root]) == root for root in roots):
        raise ValueError(
            f"log_path must stay under configured SSH log roots: {', '.join(roots)}"
        )
    return normalized


def build_remote_log_tail_command(log_path: str, lines: int) -> str:
    safe_path = normalize_remote_log_path(log_path)
    safe_lines = max(1, min(int(lines), 1000))
    roots = get_ssh_log_roots()
    case_patterns = "|".join(
        f"{shlex.quote(root)}|{shlex.quote(root)}/*" for root in roots
    )
    quoted_path = shlex.quote(safe_path)
    return "\n".join(
        [
            f'resolved="$(readlink -f -- {quoted_path} 2>/dev/null || true)"',
            f'if [ -z "$resolved" ] || [ ! -f "$resolved" ]; then echo "{LOG_FILE_NOT_FOUND_MARKER}"; exit 0; fi',
            'case "$resolved" in',
            f"  {case_patterns}) ;;",
            f'  *) echo "{LOG_PATH_FORBIDDEN_MARKER}"; exit 0 ;;',
            "esac",
            f'tail -n {safe_lines} -- "$resolved" 2>/dev/null || echo "{LOG_FILE_NOT_FOUND_MARKER}"',
        ]
    )


def create_ssh_client(
    *,
    hostname: str,
    port: int,
    username: str,
    password: str = "",
    key_path: str = "",
    known_hosts_path: str = "",
    timeout: int = 30,
):
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    if known_hosts_path:
        resolved_known_hosts = Path(known_hosts_path).expanduser()
        if not resolved_known_hosts.is_file():
            raise FileNotFoundError(
                f"known_hosts file not found: {resolved_known_hosts}"
            )
        client.load_host_keys(str(resolved_known_hosts))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())

    connect_kwargs = {
        "hostname": hostname,
        "port": port,
        "username": username,
        "timeout": timeout,
        "allow_agent": False,
        "look_for_keys": False,
    }
    if key_path:
        resolved_key_path = Path(key_path).expanduser()
        if not resolved_key_path.exists():
            raise FileNotFoundError(f"SSH private key not found: {resolved_key_path}")
        connect_kwargs["key_filename"] = str(resolved_key_path)
    elif password:
        connect_kwargs["password"] = password
    else:
        raise SshSecurityConfigError("no SSH password or key configured")

    client.connect(**connect_kwargs)
    return client


def _normalize_posix_root(root: str) -> str:
    normalized = posixpath.normpath(root.strip())
    if not normalized.startswith("/"):
        raise SshSecurityConfigError(
            f"SSH log root must be an absolute Linux path: {root}"
        )
    return normalized


def _get_fernet() -> Fernet:
    key = os.getenv("SSH_CREDENTIALS_FERNET_KEY", "").strip()
    if not key:
        if os.getenv("TESTING") == "1":
            key = _TEST_FERNET_KEY
        else:
            raise SshSecurityConfigError(
                "SSH_CREDENTIALS_FERNET_KEY not configured"
            )
    return Fernet(key.encode("utf-8"))
