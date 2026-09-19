"""Startup gates extracted from ``main`` (#736).

Version guard (refuse too-old Agent builds), ADB fork-server reconcile shared
with ``reload_config``, and legacy AEE state namespace migration.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Dict

from . import __version__ as agent_version
from . import device_discovery
from .aee.state_migration import migrate_legacy_aee_state_keys
from .heartbeat import send_heartbeat

logger = logging.getLogger(__name__)


def migrate_legacy_aee_state_on_startup(db_path: str) -> Dict[str, Any]:
    """Promote legacy scan_aee state into watcher:aee namespace during agent startup."""
    summary = migrate_legacy_aee_state_keys(db_path)
    if (
        int(summary["processed_entries_migrated"]) > 0
        or int(summary["pending_pull_migrated"]) > 0
    ):
        logger.info(
            "startup_aee_state_namespace_migrated db_path=%s summary=%s",
            db_path,
            summary,
        )
    if summary.get("errors"):
        logger.warning(
            "startup_aee_state_namespace_migration_errors db_path=%s errors=%s",
            db_path,
            summary["errors"],
        )
    return summary


def version_lt(a: str, b: str) -> bool:
    """Compare two SemVer strings (no pre-release tags). Returns True if a < b."""
    try:
        parts_a = [int(x) for x in a.split(".")]
        parts_b = [int(x) for x in b.split(".")]
    except (ValueError, TypeError):
        return False  # Malformed versions → don't block
    # Pad shorter list with zeros
    max_len = max(len(parts_a), len(parts_b))
    parts_a += [0] * (max_len - len(parts_a))
    parts_b += [0] * (max_len - len(parts_b))
    return parts_a < parts_b


def check_agent_version(api_url: str, host_id: str, mount_points, host_info) -> None:
    """Send a single heartbeat and verify agent version meets backend's minimum.

    Exits the process if the agent is too old.
    """
    try:
        resp = send_heartbeat(
            api_url,
            host_id,
            mount_points,
            host_info=host_info,
            agent_version=agent_version,
        )
    except Exception:
        logger.warning("version_check_heartbeat_failed — skipping version guard")
        return

    if resp is None:
        logger.warning("version_check_no_response — skipping version guard")
        return

    min_version = (resp.get("agent_min_version") or "").strip()
    if not min_version:
        return  # Backend doesn't enforce a minimum version yet

    if version_lt(agent_version, min_version):
        logger.critical(
            "agent_version_too_old agent=%s required=%s — refusing to start",
            agent_version, min_version,
        )
        sys.exit(1)

    logger.info("version_check_ok agent=%s min=%s", agent_version, min_version)


def ensure_adb_server_on_startup(adb_path: str) -> bool:
    """Reconcile ADB fork-servers to the Agent's configured port.

    启动与 reload_config 共用：清理非目标端口的游离 daemon 并重启目标端口
    server，让全部 USB 设备重新注册。失败不阻塞启动/热更新，由心跳健康检查
    （adb_multiple_servers / device 数）兜底告警。
    """
    try:
        result = device_discovery.ensure_single_adb_server(adb_path)
        logger.info(
            "adb_server_reconciled port=%s killed_ports=%s started=%s skipped=%s",
            result.get("port"),
            [server.get("port") for server in result.get("killed", [])],
            result.get("started"),
            result.get("skipped"),
        )
        return True
    except Exception as exc:
        logger.error("adb_server_reconcile_failed: %s", exc)
        return False
