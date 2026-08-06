"""Path resolution for AEE artifacts."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


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


def get_aee_nfs_root() -> Path:
    """控制面 15.4 CIFS/NFS 上送根路径（方案 C；`upload_manager.py` 用作 dedup/devices 上送目标）。

    priority: STP_AEE_NFS_ROOT > STP_WATCHER_NFS_BASE_DIR > STP_NFS_ROOT/sonic_tinno
    > /mnt/hdd/aee_events（无 CIFS 配置时的本地兜底，非预期生产路径）。
    """
    for env_key in ("STP_AEE_NFS_ROOT", "STP_WATCHER_NFS_BASE_DIR"):
        raw = (os.getenv(env_key) or "").strip()
        if raw:
            return Path(raw)
    nfs_root = (os.getenv("STP_NFS_ROOT") or "").strip()
    if nfs_root:
        return Path(nfs_root) / "sonic_tinno"
    return Path("/mnt/hdd/aee_events")


def get_aee_local_root() -> Path:
    """Agent 本地 HDD 根 — AEE 设备日志第一落点。

    priority: STP_AEE_LOCAL_ROOT > STP_AEE_NFS_ROOT > STP_WATCHER_NFS_BASE_DIR > STP_NFS_ROOT/sonic_tinno
    > /mnt/hdd/aee_events（方案 C 默认）。
    """
    for env_key in ("STP_AEE_LOCAL_ROOT", "STP_AEE_NFS_ROOT", "STP_WATCHER_NFS_BASE_DIR"):
        raw = (os.getenv(env_key) or "").strip()
        if raw:
            return Path(raw)
    nfs_root = (os.getenv("STP_NFS_ROOT") or "").strip()
    if nfs_root:
        return Path(nfs_root) / "sonic_tinno"
    return Path("/mnt/hdd/aee_events")


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


def get_or_create_run_date_stamp(state_store: Any, job_id: int) -> str:
    """Persist MMDD stamp per job (aligned with monolithic argv[1])."""
    key = f"aee:{job_id}:run_date_stamp"
    existing = state_store.get_state(key, "")
    if existing:
        return existing
    stamp = datetime.now().strftime("%m%d")
    state_store.set_state(key, stamp)
    return stamp


# ── 15.4 共享根路径约定（#172 统一入口）────────────────────────────────────
#
# 对象族分两类，避免继续用"哪段代码写哪里"的方式散落：
#   - JobArtifact 文件（watcher puller 默认落点 + LOCAL promote）→ jobs/{job_id}/
#   - AEE 事件目录（upload_manager / HddSpillMonitor）→ devices/{相对路径}/
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
    """UploadManager 事件目录上送目标：``{nfs_root}/devices/{plan_run_id}/``。"""
    return Path(nfs_root) / "devices" / str(int(plan_run_id))


def resolve_spill_devices_dest(
    cifs_root: Path | str,
    hdd_root: Path | str,
    local_event_dir: Path | str,
) -> Path:
    """HddSpillMonitor 上送目标：``{cifs_root}/devices/{hdd 相对路径}``。"""
    local = Path(local_event_dir).resolve()
    rel = local.relative_to(Path(hdd_root).resolve())
    return Path(cifs_root) / "devices" / rel
