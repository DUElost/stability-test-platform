"""Agent process identity / HOST_ID bootstrap extracted from ``main`` (#736).

Owns identity logging (ADR-0019/0020/0040) and HOST_ID resolve-or-auto-register
(ADR-0042). ``main`` keeps ``ensure_dirs`` and the rest of the process lifecycle.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from . import __version__ as _agent_pkg_version
from .aee.paths import resolve_shared_storage_root
from .host_registry import auto_register_host, get_host_info, load_required_host_id
from .identity import generate_agent_instance_id, read_boot_id
from .settings import get_registration_settings
from .version_info import read_agent_code_revision, read_artifact_digest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentProcessIdentity:
    """Resolved identity + env knobs needed immediately after process start."""

    host_info: Dict[str, Any]
    agent_instance_id: str
    boot_id: str
    host_id: str
    agent_version: str
    agent_code_revision: str
    agent_artifact_digest: Optional[str]
    poll_interval: float
    mount_points: List[str]
    adb_path: str
    agent_secret: str


def resolve_monitored_mount_points() -> List[str]:
    """Build heartbeat mount check paths from ``MOUNT_POINTS`` plus ``STP_AEE_NFS_ROOT``.

    When ``STP_AEE_NFS_ROOT`` is unset or empty, preserves the legacy
    ``[p for p in MOUNT_POINTS.split(",") if p]`` behavior (no whitespace
    trimming or deduplication). When ``STP_AEE_NFS_ROOT`` is configured, keeps
    all existing ``MOUNT_POINTS`` entries and appends ``STP_AEE_NFS_ROOT`` if not
    already represented (#4077).
    """
    points = [p for p in os.getenv("MOUNT_POINTS", "").split(",") if p]
    shared_root = resolve_shared_storage_root()
    if not shared_root:
        return points

    seen = {(item.strip().rstrip("/") or "/") for item in points if item.strip()}
    norm_shared = shared_root.rstrip("/") or "/"
    if norm_shared not in seen:
        points.append(shared_root)
    return points


def resolve_or_register_host_id(api_url: str, host_info: Dict[str, Any]) -> str:
    """Load ``HOST_ID`` or auto-register with retry (ADR-0042).

    Registration settings are read only on the failure / auto-register paths so
    a healthy ``HOST_ID`` is never blocked by unused register knobs
    (``test_registration_settings_read_stays_deferred``).
    """
    try:
        host_id = load_required_host_id()
    except ValueError as exc:
        # 检查是否启用自动注册
        if get_registration_settings().auto_register_enabled:
            host_id = None  # will be resolved in the retry loop below
        else:
            logger.error(
                "invalid_host_id_config",
                extra={
                    "host_id_raw": os.getenv("HOST_ID"),
                    "error": str(exc),
                },
            )
            logger.error(
                "Set HOST_ID to an IP-derived id (e.g. 198-51-100-6), or set "
                "AUTO_REGISTER_HOST=true to auto-register"
            )
            raise SystemExit(2) from exc

    # 如果 host_id 为 None（自动注册模式），带重试地注册
    if host_id is None:
        reg_settings = get_registration_settings()
        max_retries = reg_settings.auto_register_max_retries  # 0 = infinite
        retry_delay = reg_settings.auto_register_retry_delay
        attempt = 0
        while True:
            attempt += 1
            try:
                host_id = auto_register_host(api_url, host_info)
                break
            except Exception as exc:
                if max_retries and attempt >= max_retries:
                    logger.error(
                        "auto_register_failed after %d attempts: %s", attempt, exc
                    )
                    raise SystemExit(2) from exc
                logger.warning(
                    "auto_register_retry attempt=%d delay=%.0fs error=%s",
                    attempt,
                    retry_delay,
                    exc,
                )
                time.sleep(retry_delay)
    return str(host_id)


def bootstrap_process_identity(api_url: str) -> AgentProcessIdentity:
    """Collect host info, identity digests, and a resolved ``host_id``."""
    # 获取本机信息（需要在验证 HOST_ID 之前）
    host_info = get_host_info()

    # ADR-0019 Phase 3a: generate agent identity
    agent_instance_id = generate_agent_instance_id()
    boot_id = read_boot_id()
    agent_code_revision = read_agent_code_revision()
    # ADR-0040 D2：此处启动读取仅用于身份日志；心跳上报值由 HeartbeatThread
    # 逐拍重读（#1943——write-digest 在重启探活后落盘，启动单读永远落后
    # 一轮，no-op 稳态无法建立）。
    agent_artifact_digest = read_artifact_digest()
    logger.info(
        "agent_identity instance=%s boot=%s version=%s code_revision=%s artifact_digest=%s",
        agent_instance_id,
        boot_id,
        _agent_pkg_version,
        agent_code_revision or "(none)",
        agent_artifact_digest or "(none)",
    )

    # 加载 HOST_ID，支持自动注册（ADR-0042 P2 #3：旋钮由 Settings 承载；
    # 取值点保持迁移前的惰性时机——HOST_ID 正常时不解析注册旋钮）
    host_id = resolve_or_register_host_id(api_url, host_info)
    poll_interval = float(os.getenv("POLL_INTERVAL", "5"))
    mount_points = resolve_monitored_mount_points()
    adb_path = os.getenv("ADB_PATH", "adb")
    agent_secret = os.getenv("AGENT_SECRET", "")

    logger.info(
        "agent_started",
        extra={"host_id": host_id, "api_url": api_url, "ip": host_info["ip"]},
    )

    return AgentProcessIdentity(
        host_info=host_info,
        agent_instance_id=agent_instance_id,
        boot_id=boot_id,
        host_id=host_id,
        agent_version=_agent_pkg_version,
        agent_code_revision=agent_code_revision or "",
        agent_artifact_digest=agent_artifact_digest,
        poll_interval=poll_interval,
        mount_points=mount_points,
        adb_path=adb_path,
        agent_secret=agent_secret,
    )
