"""NFS path resolution for AEE artifacts."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


def get_aee_nfs_root() -> Path:
    """sonic_tinno root — priority: STP_AEE_NFS_ROOT > STP_WATCHER_NFS_BASE_DIR > STP_NFS_ROOT/sonic_tinno."""
    for env_key in ("STP_AEE_NFS_ROOT", "STP_WATCHER_NFS_BASE_DIR"):
        raw = (os.getenv(env_key) or "").strip()
        if raw:
            return Path(raw)
    nfs_root = (os.getenv("STP_NFS_ROOT") or "").strip()
    if nfs_root:
        return Path(nfs_root) / "sonic_tinno"
    return Path("/mnt/storage/test-platform/sonic_tinno")


def resolve_device_output_dir(
    *,
    nfs_root: Path,
    folder_name: str,
    serial: str,
) -> Path:
    """{sonic_tinno}/{folder_name}/{serial}/"""
    return nfs_root / folder_name / serial


def resolve_sonic_output_dir_for_job(
    *,
    adb: Any,
    serial: str,
    job_id: int,
    state_store: Any,
    nfs_root: Optional[Path] = None,
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
    root = nfs_root or get_aee_nfs_root()
    out = resolve_device_output_dir(nfs_root=root, folder_name=folder_name, serial=serial)
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
