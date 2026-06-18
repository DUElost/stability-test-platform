"""Correlate mobilelog files with AEE timestamps — aligned with export_correlated_mobilelogs."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .paths import resolve_mobilelog_subdir
from .timestamp import parse_mobilelog_filename_to_datetime, parse_timestamp

logger = logging.getLogger(__name__)

PullFn = Callable[[str, str, int], bool]
ShellFn = Callable[[str, int], Optional[str]]

DEFAULT_LOG_TYPES = {
    "MAIN": {"prefix": "main_log", "enabled": True},
    "KERNEL": {"prefix": "kernel_log", "enabled": True},
    "SYS": {"prefix": "sys_log", "enabled": False},
}


def _resolve_mobilelog_subdir() -> str:
    """D3/T0.5-2: 默认 correlated_mobilelogs/(对齐 monolith);
    env 逃生口 STP_WATCHER_AEE_SUBDIR_LAYOUT=stp 回退 mobilelog/。

    实现复用 paths.resolve_mobilelog_subdir(单一事实源,与 bugreport 逃生口一致)。
    """
    return resolve_mobilelog_subdir()


def export_correlated_mobilelogs(
    *,
    aee_ts_str: str,
    output_dir: Path,
    remote_mobilelog_path: str,
    shell_fn: ShellFn,
    pull_fn: PullFn,
    log_types: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, int]:
    """Pull mobilelog files around AEE timestamp into output_dir (mobilelog subdir).

    ADR-0018 2026-06-18: output_dir 由调用方传入事件目录(local_target_dir),
    mobilelog 落在 output_dir/mobilelog/(或 correlated_mobilelogs/,由 env 控制)。
    """
    metrics = {"matched": 0, "pulled": 0, "files_selected": 0}
    aee_dt = parse_timestamp(aee_ts_str)
    if not aee_dt:
        logger.warning("mobilelog_skip_unparseable_ts ts=%s", aee_ts_str)
        return metrics

    mobilelog_dir = output_dir / _resolve_mobilelog_subdir()
    mobilelog_dir.mkdir(parents=True, exist_ok=True)

    remote_root = remote_mobilelog_path.rstrip("/") + "/"
    types = log_types or DEFAULT_LOG_TYPES

    for _log_type, config in types.items():
        if not config.get("enabled", True):
            continue
        prefix = config["prefix"]
        find_cmd = f"find {remote_root} -name '{prefix}*' -type f -print0"
        output = shell_fn(find_cmd, 120)
        if not output:
            continue

        file_infos: List[Dict[str, Any]] = []
        for path in output.split("\0"):
            if not path:
                continue
            dt = parse_mobilelog_filename_to_datetime(os.path.basename(path))
            if dt:
                file_infos.append({"path": path, "timestamp": dt})

        if not file_infos:
            continue

        file_infos.sort(key=lambda x: x["timestamp"])
        target_idx = next(
            (i for i, f in enumerate(file_infos) if f["timestamp"] > aee_dt),
            len(file_infos),
        )
        start_idx = max(0, target_idx - 2)
        end_idx = min(len(file_infos), target_idx + 2)
        selected = file_infos[start_idx:end_idx]
        metrics["files_selected"] += len(selected)

        for file_info in selected:
            local_path = mobilelog_dir / os.path.basename(file_info["path"])
            if local_path.exists():
                metrics["matched"] += 1
                continue
            if pull_fn(file_info["path"], str(local_path), 180):
                metrics["pulled"] += 1
                metrics["matched"] += 1

    return metrics


def make_adb_pull_fn(serial: str, adb_path: str = "adb") -> PullFn:
    def _pull(remote: str, local: str, timeout: int) -> bool:
        local_path = Path(local)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = subprocess.run(
                [adb_path, "-s", serial, "pull", remote, local],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.returncode == 0 and local_path.exists()
        except (subprocess.TimeoutExpired, OSError):
            return False

    return _pull


def make_adb_shell_fn(serial: str, adb_path: str = "adb") -> ShellFn:
    def _shell(cmd: str, timeout: int) -> Optional[str]:
        try:
            result = subprocess.run(
                [adb_path, "-s", serial, "shell", cmd],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode != 0:
                return None
            return result.stdout or ""
        except (subprocess.TimeoutExpired, OSError):
            return None

    return _shell
