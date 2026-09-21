"""Startup gates extracted from ``main`` (#736).

Version guard (refuse too-old Agent builds), ADB fork-server reconcile shared
with ``reload_config``, legacy AEE state namespace migration, and the
single-instance lock (#2961).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from . import __version__ as agent_version
from . import device_discovery
from .aee.state_migration import migrate_legacy_aee_state_keys
from .config import LOG_DIR
from .heartbeat import send_heartbeat

try:  # Windows/WSL 开发机没有 fcntl —— 守卫降级为不生效（见 enforce_single_instance）
    import fcntl
except ImportError:  # pragma: no cover - 平台分支
    fcntl = None  # type: ignore[assignment]

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


DEFAULT_LOCK_FILENAME = "agent.lock"


def _lock_file_path(override: Optional[str]) -> Path:
    return Path(override) if override else Path(LOG_DIR) / DEFAULT_LOCK_FILENAME


def _read_lock_holder_pid(path: Path) -> str:
    """读锁文件里记的持有者 pid（仅供日志诊断，不参与判定）。"""
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                return line.strip()
    except OSError:
        pass
    return "unknown"


def enforce_single_instance(
    lock_path: Optional[str] = None, *, exit_code: int = 1
) -> Optional[int]:
    """同机单实例守卫：第二个 Agent 进程立即失败退出（#2961）。

    **为什么在进程入口**：2026-07-27，19/20 台 host 上 systemd 托管实例与一条
    手工 ``venv/bin/python -m agent.main`` 并存。两者同 ``HOST_ID``、各持不同的
    ``agent_instance_id``，每次心跳互相覆盖 ``host.last_agent_instance_id``，
    于是 coordinator fencing 对任一实例都判 stale，全平台 Coordinator 心跳被拒
    3 天。守护进程管不到手工进程，所以守卫必须落在**任何启动方式都会经过的
    入口**，而不是 systemd unit 或启动脚本里。

    **为什么是 flock 而不是 pidfile**：内核在进程退出时自动释放，锁文件残留
    不会阻塞下一次启动，也不存在「陈旧 pid 被复用」的误判。锁 fd 带
    ``O_CLOEXEC``：Agent 派生的子进程（adb fork-server / 脚本）不继承锁，
    否则子进程多活一会儿就会顶住锁，把 systemd 的 ``Restart=always``
    拖成启动失败。

    **失败方向**：锁文件建不出来（目录不可写、路径被占等）只记 warning 并继续
    启动 —— 守卫是纵深防御，不能因文件系统问题让整机 Agent 起不来。真正被
    占用时才是 fail-fast：记 CRITICAL（含持有者 pid）后 ``sys.exit``，让
    ``systemctl status`` 与日志同时可见，而不是两个实例静默叠加心跳。

    Returns:
        持有锁的 fd（进程存活期间保持打开即保持锁定；``os.open`` 返回的 int
        不会被 GC 关掉）。守卫降级时为 ``None``。
    """
    if fcntl is None:  # pragma: no cover - Windows/WSL 开发机
        logger.warning("single_instance_guard_skipped reason=no_fcntl")
        return None

    path = _lock_file_path(lock_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o644)
    except OSError as exc:
        logger.warning(
            "single_instance_guard_unavailable path=%s err=%s — 跳过守卫继续启动",
            path,
            exc,
        )
        return None

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        holder = _read_lock_holder_pid(path)
        logger.critical(
            "agent_already_running lock=%s holder_pid=%s — 同机已有 Agent 进程，"
            "拒绝启动第二个实例（同 HOST_ID 双实例会让 coordinator 心跳全被拒）",
            path,
            holder,
        )
        os.close(fd)
        sys.exit(exit_code)

    try:
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
    except OSError:  # pid 只是诊断信息，写不上不影响守卫生效
        logger.debug("single_instance_pid_write_failed path=%s", path, exc_info=True)
    logger.info("single_instance_lock_acquired lock=%s pid=%s", path, os.getpid())
    return fd


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
