"""Path resolution for AEE artifacts."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_AEE_LOCAL_ROOT_DEFAULT = "/mnt/hdd/aee_events"


def resolve_shared_storage_root() -> str:
    """中心存储本机挂载点（NFS = CIFS = 同一台分享）。未配置返回空串。

    唯一主键 ``STP_AEE_NFS_ROOT``（#289：CIFS/WATCHER 弃用别名回落已删除，
    未设主键即视为未配置）。不回落到 ``STP_NFS_ROOT`` 或 HDD。

    控制面请用 ``backend.core.storage_root.resolve_shared_storage_root``
    （避免 core → agent.aee）。此处副本供 Agent 独立安装使用，语义须保持一致。
    """
    return (os.getenv("STP_AEE_NFS_ROOT") or "").strip()


def get_aee_nfs_root() -> Path:
    """中心存储挂载点。未设 ``STP_AEE_NFS_ROOT`` 时抛错。"""
    raw = resolve_shared_storage_root()
    if not raw:
        raise RuntimeError("STP_AEE_NFS_ROOT is not set")
    return Path(raw)


def _default_ssd_fallback_root() -> Path:
    try:
        from backend.agent.config import LOG_DIR
    except ImportError:
        from agent.config import LOG_DIR
    return LOG_DIR / "aee_events"


def _mount_fstype_for_path(path: Path) -> Optional[str]:
    """Return fstype for the longest matching mount point in /proc/mounts."""
    try:
        resolved = path.resolve(strict=False)
        mounts_text = Path("/proc/mounts").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    best_mount = ""
    best_fstype: Optional[str] = None
    resolved_str = str(resolved)
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        mount_point = parts[1].replace("\\040", " ")
        fstype = parts[2]
        mp = mount_point.rstrip("/") or "/"
        if resolved == Path(mount_point) or resolved_str.startswith(mp + "/") or (
            mp == "/" and resolved_str.startswith("/")
        ):
            if len(mount_point) >= len(best_mount):
                best_mount = mount_point
                best_fstype = fstype
    return best_fstype


def _is_writable_hdd_root(path: Path) -> bool:
    """HDD 候选根：存在、可写，且挂载类型不是 tmpfs（ADR-0028 D5）。"""
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        return False
    if not resolved.exists():
        return False
    if not os.access(resolved, os.W_OK):
        return False
    if _mount_fstype_for_path(resolved) == "tmpfs":
        return False
    return True


_ssd_fallback_logged = False


def get_aee_local_root() -> Path:
    """Agent 本地 HDD/SSD 根 — AEE 设备日志第一落点（ADR-0028 D5）。

    解析链（不回落中心存储键）::

        STP_AEE_LOCAL_ROOT（可写且非 tmpfs）
          → STP_AEE_SSD_FALLBACK_ROOT 或 {LOG_DIR}/aee_events
          → /mnt/hdd/aee_events
    """
    global _ssd_fallback_logged

    configured = (os.getenv("STP_AEE_LOCAL_ROOT") or "").strip()
    if configured:
        candidate = Path(configured)
        if _is_writable_hdd_root(candidate):
            return candidate
        logger.warning("aee_local_root_unusable path=%s", configured)

    ssd_env = (os.getenv("STP_AEE_SSD_FALLBACK_ROOT") or "").strip()
    ssd = Path(ssd_env) if ssd_env else _default_ssd_fallback_root()
    try:
        ssd.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    if os.access(ssd, os.W_OK):
        if not _ssd_fallback_logged:
            _ssd_fallback_logged = True
            logger.info("aee_local_root_ssd_fallback path=%s", ssd)
        return ssd

    default_hdd = Path(_AEE_LOCAL_ROOT_DEFAULT)
    if _is_writable_hdd_root(default_hdd):
        return default_hdd
    return default_hdd


def is_ssd_fallback_root(path: Path | str) -> bool:
    """True when *path* is the configured SSD fallback root (disables HddSpill)."""
    try:
        resolved = Path(path).resolve(strict=False)
    except OSError:
        return False
    ssd_env = (os.getenv("STP_AEE_SSD_FALLBACK_ROOT") or "").strip()
    try:
        ssd = (
            Path(ssd_env).resolve(strict=False)
            if ssd_env
            else _default_ssd_fallback_root().resolve(strict=False)
        )
    except OSError:
        return False
    return resolved == ssd


def iter_aee_local_root_candidates() -> list[Path]:
    """D5 链上所有可能的事件本地根（用于校验已创建事件的 ``local_path``）。"""
    candidates: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        try:
            key = str(path.resolve(strict=False))
        except OSError:
            return
        if key in seen:
            return
        seen.add(key)
        candidates.append(Path(key))

    configured = (os.getenv("STP_AEE_LOCAL_ROOT") or "").strip()
    if configured:
        _add(Path(configured))
    ssd_env = (os.getenv("STP_AEE_SSD_FALLBACK_ROOT") or "").strip()
    _add(Path(ssd_env) if ssd_env else _default_ssd_fallback_root())
    _add(Path(_AEE_LOCAL_ROOT_DEFAULT))
    return candidates


class PathOutsideRootError(ValueError):
    """Raised when a path escapes the configured AEE local root."""


def resolve_path_under_aee_local(raw: str, *, root: Path | None = None) -> Path:
    """Resolve *raw* under an AEE local root; reject ``..`` / symlink escape.

    未指定 *root* 时，在 D5 候选根链中匹配（事件创建后根切换仍可校验）。
    """
    try:
        resolved = Path(raw).expanduser().resolve(strict=False)
    except OSError as exc:
        raise PathOutsideRootError(str(raw)) from exc
    if root is not None:
        root_resolved = root.resolve(strict=False)
        if not resolved.is_relative_to(root_resolved):
            raise PathOutsideRootError(f"{resolved} is not under {root_resolved}")
        return resolved
    for candidate in iter_aee_local_root_candidates():
        if resolved.is_relative_to(candidate):
            return resolved
    raise PathOutsideRootError(f"{resolved} is not under any configured AEE local root")


def _aee_subdir_layout() -> str:
    """D3: 子目录布局开关。`stp`(默认,ADR-0025 事件目录聚合) / `correlated`(逃生口,对齐 monolith 旧布局)。"""
    return (os.environ.get("STP_WATCHER_AEE_SUBDIR_LAYOUT", "stp") or "").strip().lower()


def resolve_mobilelog_subdir() -> str:
    """关联 mobilelog 落盘子目录名。

    默认 `mobilelog/`(ADR-0025 D3 契约：按事件目录聚合);env STP_WATCHER_AEE_SUBDIR_LAYOUT=correlated
    回退旧布局 `correlated_mobilelogs/`。
    """
    return "correlated_mobilelogs" if _aee_subdir_layout() == "correlated" else "mobilelog"


def resolve_bugreport_subdir() -> str:
    """bugreport 落盘子目录名。

    默认 `bugreport/`(ADR-0025 D3 契约：按事件目录聚合);env STP_WATCHER_AEE_SUBDIR_LAYOUT=correlated
    回退旧布局 `correlated_bugreports/`。
    """
    return "correlated_bugreports" if _aee_subdir_layout() == "correlated" else "bugreport"


def resolve_device_output_dir(
    *,
    local_root: Path,
    folder_name: str,
    serial: str,
) -> Path:
    """{local_root}/{folder_name}/{serial}/"""
    return local_root / folder_name / serial


def resolve_sonic_output_dir_for_job(
    *,
    adb: Any,
    serial: str,
    job_id: int,
    state_store: Any,
    local_root: Optional[Path] = None,
) -> Optional[Path]:
    """Compute per-job sonic_tinno device dir for Watcher LogPuller."""
    from .folder_name import get_aee_log_folder_name, make_getprop_from_shell

    def _shell(cmd: str, timeout: int = 10) -> str:
        try:
            result = adb.shell(serial, cmd, timeout=timeout)
            return (getattr(result, "stdout", None) or result or "").strip()
        except Exception:
            return ""

    stamp = get_or_create_run_date_stamp(state_store, job_id)
    folder_name = get_aee_log_folder_name(
        getprop=make_getprop_from_shell(lambda cmd, timeout: _shell(cmd, timeout)),
        run_date_stamp=stamp,
    )
    if not folder_name:
        return None
    root = local_root or get_aee_local_root()
    out = resolve_device_output_dir(local_root=root, folder_name=folder_name, serial=serial)
    out.mkdir(parents=True, exist_ok=True)
    return out


def shanghai_mmdd(now: datetime | None = None) -> str:
    """MMDD in Asia/Shanghai — same clock as control-plane scan_now stamps."""
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_SHANGHAI).strftime("%m%d")


def get_or_create_run_date_stamp(
    state_store: Any,
    job_id: int,
    run_date_stamp: Optional[str] = None,
) -> str:
    """Persist MMDD stamp per job (Asia/Shanghai, not the Agent host TZ).

    ``run_date_stamp`` 非空时视为权威值（来自控制面 job started_at，与
    plan_run_scan_scope 同一时钟）：直接持久化并返回，覆盖可能已在 Agent
    本地回退写入的旧值，避免跨午夜后 AEE 目录 MMDD 与控制面扫描范围漂移。
    """
    key = f"aee:{job_id}:run_date_stamp"
    existing = state_store.get_state(key, "")
    if run_date_stamp:
        if existing and existing != run_date_stamp:
            logger.warning(
                "aee_run_date_stamp_replaced job=%d old=%s new=%s",
                job_id, existing, run_date_stamp,
            )
        state_store.set_state(key, run_date_stamp)
        return run_date_stamp
    if existing:
        return existing
    stamp = shanghai_mmdd()
    state_store.set_state(key, stamp)
    return stamp


# ── 15.4 共享根路径约定（#172 统一入口）────────────────────────────────────
#
# 对象族分两类，避免继续用"哪段代码写哪里"的方式散落：
#   - JobArtifact 文件（watcher puller 默认落点 + LOCAL promote）→ jobs/{job_id}/
#   - AEE 事件目录（EventUploader / DLE；含 HddSpill enqueue）→
#     devices/{plan_run_id}/ 或 devices/unassigned/{event_id}/
# 控制面 download 与 dedup extract 分别消费这两族；任何一端改布局都必须改这里。


def resolve_artifact_promote_dir(shared_root: Path | str, job_id: int) -> Path:
    """LOCAL artifact promote 目标：``{shared_root}/jobs/{job_id}/``（#97/#172）。"""
    return Path(shared_root) / "jobs" / str(int(job_id))


def resolve_puller_artifact_dir(
    nfs_base_dir: Path | str,
    job_id: int,
    category: str,
) -> Path:
    """Watcher LogPuller 默认落盘目录：``{nfs_base}/jobs/{job_id}/{category}/``。"""
    return Path(nfs_base_dir) / "jobs" / str(int(job_id)) / category


def resolve_upload_devices_dir(nfs_root: Path | str, plan_run_id: int) -> Path:
    """UploadManager / EventUploader 事件目录上送目标：``{nfs_root}/devices/{plan_run_id}/``。"""
    return Path(nfs_root) / "devices" / str(int(plan_run_id))
