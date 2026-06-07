"""Incremental AEE db_history pull — aligned with process_device_logs (without decrypt)."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from backend.core.aee_metadata import (
    infer_aee_subtype_from_paths,
    normalize_package_name,
    parse_exp_main_summary,
)

from .bugreport import export_bugreport_for_timestamp
from .db_history import (
    load_processed_lines,
    parse_effective_db_history_line,
    save_processed_lines,
    state_key,
)
from .folder_name import get_aee_log_folder_name, make_getprop_from_shell
from .mobilelog import export_correlated_mobilelogs, make_adb_pull_fn, make_adb_shell_fn
from .paths import get_aee_nfs_root, get_or_create_run_date_stamp, resolve_device_output_dir
from .timestamp import format_timestamp_for_filename, parse_timestamp

logger = logging.getLogger(__name__)

DEFAULT_AEE_PATHS = ["/data/aee_exp", "/data/vendor/aee_exp"]
PullFn = Callable[[str, str, int], bool]
ShellFn = Callable[[str, int], Optional[str]]


@dataclass
class ProcessConfig:
    aee_paths: List[str] = field(default_factory=lambda: list(DEFAULT_AEE_PATHS))
    remote_mobilelog_path: str = "/data/debuglogger/mobilelog/"
    filter_db_logs: bool = False
    whitelist: Optional[Set[str]] = None
    export_mobilelog: bool = True
    export_bugreport: bool = True
    bugreport_cooldown_seconds: int = 300
    bugreport_cooldown_event_types: Optional[set[str]] = None
    bugreport_timeout_seconds: int = 600
    state_key_prefix: str = "scan_aee"
    pull_timeout_seconds: int = 300
    pull_retry_limit: int = 10
    max_entries_per_run: Optional[int] = None


@dataclass
class ProcessResult:
    scanned: int = 0
    pulled: int = 0
    skipped_known: int = 0
    new_timestamps: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    pending_remaining: int = 0


def process_device_logs(
    *,
    serial: str,
    job_id: int,
    state_store: Any,
    adb_path: str = "adb",
    config: Optional[ProcessConfig] = None,
    nfs_root: Optional[Path] = None,
    run_date_stamp: Optional[str] = None,
    on_new_entry: Optional[Callable[[Dict[str, Any]], None]] = None,
    shell_fn: Optional[ShellFn] = None,
    pull_fn: Optional[PullFn] = None,
    stop_event: Optional[threading.Event] = None,
) -> ProcessResult:
    """Diff db_history, pull new AEE dirs, correlate mobilelog + bugreport.

    on_new_entry (M0/PR #2): optional callback invoked once per successfully pulled
    new entry. Payload dict shape::
        {
            "line":          str  # original db_history line
            "parsed":        Dict[str, str]  # db_path / pkg_name / timestamp / event_type / raw_event_type / event_subtype
            "aee_type":      str  # "aee_exp" | "vendor_aee_exp"
            "output_subdir": Path  # NFS dir where AEE files were pulled to
        }
    Patrol path passes None → behavior unchanged. Reconciler path uses it to emit
    log_signal with extra={event_type, package_name, aee_ts, nfs_path, pull_source}.
    """
    cfg = config or ProcessConfig()
    result = ProcessResult()
    shell_fn = shell_fn or make_adb_shell_fn(serial, adb_path)
    pull_fn = pull_fn or make_adb_pull_fn(serial, adb_path)
    remaining_budget = (
        int(cfg.max_entries_per_run)
        if cfg.max_entries_per_run is not None and int(cfg.max_entries_per_run) > 0
        else None
    )

    stamp = run_date_stamp or get_or_create_run_date_stamp(state_store, job_id)
    folder_name = get_aee_log_folder_name(
        getprop=make_getprop_from_shell(lambda cmd, timeout: shell_fn(cmd, timeout) or ""),
        run_date_stamp=stamp,
    )
    if not folder_name:
        result.errors.append("failed_to_resolve_aee_folder_name")
        return result

    root = nfs_root or get_aee_nfs_root()
    base_output_dir = resolve_device_output_dir(
        nfs_root=root,
        folder_name=folder_name,
        serial=serial,
    )
    base_output_dir.mkdir(parents=True, exist_ok=True)

    whitelist = cfg.whitelist or set()
    pending_key_prefix = f"{cfg.state_key_prefix}:{serial}"
    stop_requested = False

    for remote_aee_path in cfg.aee_paths:
        if stop_event is not None and stop_event.is_set():
            stop_requested = True
            logger.info("process_device_logs_stop_requested_before_scan serial=%s job=%d", serial, job_id)
            break

        remote_aee_path = remote_aee_path.rstrip("/")
        aee_type = "vendor_aee_exp" if "vendor" in remote_aee_path else "aee_exp"
        output_subdir = base_output_dir / aee_type
        output_subdir.mkdir(parents=True, exist_ok=True)

        processed_key = state_key(serial, aee_type, prefix=cfg.state_key_prefix)
        processed_lines = load_processed_lines(state_store, processed_key)

        history_content = shell_fn(f"cat {remote_aee_path}/db_history", 30)
        if history_content is None:
            result.errors.append(f"db_history_unreadable:{remote_aee_path}")
            continue

        current_lines = [ln for ln in history_content.strip().splitlines() if ln.strip()]
        result.scanned += len(current_lines)

        pending_key = f"{pending_key_prefix}:{aee_type}:pending_pull"
        pending_tasks = _load_pending_tasks(state_store, pending_key)

        passed_filter_lines: List[str] = []
        for line in current_lines:
            parsed = parse_effective_db_history_line(line, aee_type)
            if not parsed:
                continue
            is_whitelisted = (
                aee_type != "aee_exp"
                or not cfg.filter_db_logs
                or parsed["pkg_name"] in whitelist
            )
            if not is_whitelisted:
                continue
            passed_filter_lines.append(line)
            if line not in processed_lines and line not in pending_tasks:
                pending_tasks[line] = {
                    "db_path": parsed["db_path"],
                    "pkg_name": parsed["pkg_name"],
                    "timestamp": parsed["timestamp"],
                    "event_type": parsed.get("event_type", ""),
                    "raw_event_type": parsed.get("raw_event_type", ""),
                    "event_subtype": parsed.get("event_subtype", ""),
                    "retry_count": 0,
                }

        _write_text(output_subdir / "db_save_org.txt", history_content)
        _write_text(output_subdir / "db_save.txt", "\n".join(passed_filter_lines))

        def _finalize_processed_entry(
            *,
            line: str,
            parsed: Dict[str, Any],
            local_target_dir: Path,
            export_side_effects: bool,
        ) -> None:
            pending_tasks.pop(line, None)
            result.pulled += 1
            result.new_timestamps.append(parsed["timestamp"])

            if on_new_entry is not None:
                try:
                    on_new_entry({
                        "line":          line,
                        "parsed":        dict(parsed),
                        "aee_type":      aee_type,
                        "output_subdir": local_target_dir,
                    })
                except Exception:
                    logger.exception(
                        "aee_on_new_entry_callback_failed serial=%s db=%s",
                        serial, parsed.get("db_path"),
                    )

            # Persist processed/pending state before best-effort side effects so
            # slow or failing exports do not keep the whole tick in a half-finished
            # state and block later db_history increments from being observed.
            processed_lines.add(line)
            save_processed_lines(state_store, processed_key, processed_lines)
            _save_pending_tasks(state_store, pending_key, pending_tasks)

            if not export_side_effects or stop_requested:
                return

            if cfg.export_mobilelog:
                export_correlated_mobilelogs(
                    aee_ts_str=parsed["timestamp"],
                    output_dir=base_output_dir,
                    remote_mobilelog_path=cfg.remote_mobilelog_path,
                    shell_fn=shell_fn,
                    pull_fn=pull_fn,
                )

            if cfg.export_bugreport:
                export_bugreport_for_timestamp(
                    serial=serial,
                    timestamp_str=parsed["timestamp"],
                    output_dir=base_output_dir,
                    adb_path=adb_path,
                    event_type=parsed.get("event_type"),
                    cooldown_seconds=cfg.bugreport_cooldown_seconds,
                    cooldown_event_types=cfg.bugreport_cooldown_event_types,
                    timeout_seconds=cfg.bugreport_timeout_seconds,
                    stop_event=stop_event,
                )

        for line, task in _iter_pending_tasks_newest_first(pending_tasks):
            if stop_event is not None and stop_event.is_set():
                stop_requested = True
                logger.info(
                    "process_device_logs_stop_requested_mid_tick serial=%s job=%d aee_type=%s",
                    serial, job_id, aee_type,
                )
                break

            if line in processed_lines:
                pending_tasks.pop(line, None)
                continue

            parsed = task
            if not parsed.get("db_path") or not parsed.get("timestamp"):
                pending_tasks.pop(line, None)
                continue

            if int(task.get("retry_count", 0)) >= cfg.pull_retry_limit:
                pending_tasks.pop(line, None)
                result.errors.append(f"pull_retry_exceeded:{parsed['db_path']}")
                continue

            if remaining_budget is not None:
                if remaining_budget <= 0:
                    break
                remaining_budget -= 1

            dirname = (
                f"{format_timestamp_for_filename(parsed['timestamp'])}_"
                f"{os.path.basename(parsed['db_path'])}"
            )
            local_target_dir = output_subdir / dirname

            if local_target_dir.exists():
                verify_ok, verify_msg, _ = _verify_pulled_aee_log_strict(
                    local_target_dir,
                    remote_path=parsed["db_path"],
                    shell_fn=shell_fn,
                )
                if verify_ok:
                    parsed = _enrich_parsed_with_local_aee_metadata(parsed, local_target_dir)
                    _finalize_processed_entry(
                        line=line,
                        parsed=parsed,
                        local_target_dir=local_target_dir,
                        export_side_effects=False,
                    )
                    continue
                logger.info(
                    "aee_pull_existing_dir_invalid serial=%s db=%s reason=%s",
                    serial, parsed["db_path"], verify_msg,
                )
                _cleanup_dir(local_target_dir)

            if not pull_fn(parsed["db_path"], str(local_target_dir), cfg.pull_timeout_seconds):
                task["retry_count"] = int(task.get("retry_count", 0)) + 1
                task["last_error"] = "adb_pull_failed"
                _cleanup_dir(local_target_dir)
                result.errors.append(f"pull_failed:{parsed['db_path']}")
                continue

            verify_ok, verify_msg, _ = _verify_pulled_aee_log_strict(
                local_target_dir,
                remote_path=parsed["db_path"],
                shell_fn=shell_fn,
            )
            if not verify_ok:
                task["retry_count"] = int(task.get("retry_count", 0)) + 1
                task["last_error"] = f"verify_failed: {verify_msg}"
                _cleanup_dir(local_target_dir)
                result.errors.append(f"pull_verify_failed:{parsed['db_path']}:{verify_msg}")
                continue

            parsed = _enrich_parsed_with_local_aee_metadata(parsed, local_target_dir)
            _finalize_processed_entry(
                line=line,
                parsed=parsed,
                local_target_dir=local_target_dir,
                export_side_effects=True,
            )

        _save_pending_tasks(state_store, pending_key, pending_tasks)
        result.pending_remaining += len(pending_tasks)
        if stop_requested:
            break

    return result


def _dir_has_content(path: Path) -> bool:
    try:
        return path.is_dir() and any(path.iterdir())
    except OSError:
        return False


def _iter_pending_tasks_newest_first(
    pending_tasks: Dict[str, Dict[str, Any]],
) -> List[Tuple[str, Dict[str, Any]]]:
    # Prefer newer AEE rows so fresh runtime crashes are not starved by old backlog.
    return sorted(
        pending_tasks.items(),
        key=lambda item: (
            parse_timestamp(str(item[1].get("timestamp") or "")) is not None,
            parse_timestamp(str(item[1].get("timestamp") or "")),
        ),
        reverse=True,
    )


def _cleanup_dir(path: Path) -> None:
    if not path.exists():
        return
    try:
        import shutil
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def _enrich_parsed_with_local_aee_metadata(
    parsed: Dict[str, Any],
    local_target_dir: Path,
) -> Dict[str, Any]:
    enriched = dict(parsed)
    exp_main_summary = parse_exp_main_summary(local_target_dir)

    subtype = str(enriched.get("event_subtype") or "").strip()
    if not subtype or subtype == "其他":
        inferred_subtype = (
            exp_main_summary.get("event_subtype")
            or infer_aee_subtype_from_paths(
                str(enriched.get("db_path") or ""),
                str(local_target_dir),
            )
        )
        if inferred_subtype:
            enriched["event_subtype"] = inferred_subtype
            enriched["event_type"] = "ANR" if inferred_subtype == "ANR" else "CRASH"

    package_name = normalize_package_name(str(enriched.get("pkg_name") or ""))
    if not package_name:
        package_name = normalize_package_name(exp_main_summary.get("package_name", ""))
    if not package_name:
        package_name = normalize_package_name(exp_main_summary.get("current_process", ""))
    if package_name:
        enriched["pkg_name"] = package_name

    return enriched


def _verify_pulled_aee_log_strict(
    local_dir: Path,
    *,
    remote_path: Optional[str] = None,
    shell_fn: Optional[ShellFn] = None,
) -> Tuple[bool, str, bool]:
    """Strict AEE pull verification — 对齐 monolith _verify_pulled_aee_log_strict。

    返回 (success, message, remote_verified):
      - 本地目录存在 + 非空 + 至少含一个 .dbg 关键文件 + 总大小 > 0
      - 远端可达时,做文件数 / 总大小 90% 比对(monolith 等价规则)
    """
    try:
        if not local_dir.is_dir():
            return False, "本地目录不存在", False
        try:
            entries = list(local_dir.iterdir())
        except OSError as exc:
            return False, f"读取本地目录失败: {exc}", False
        if not entries:
            return False, "目录为空", False

        dbg_files: List[Path] = []
        total_size = 0
        file_count = 0
        for root_dir, _, files in os.walk(local_dir):
            for filename in files:
                fp = Path(root_dir) / filename
                try:
                    size = fp.stat().st_size
                except OSError:
                    size = 0
                total_size += size
                file_count += 1
                if filename.endswith(".dbg"):
                    dbg_files.append(fp)

        if file_count == 0:
            return False, "目录中没有文件", False
        if not dbg_files:
            return False, "缺少 dbg 关键文件", False
        if total_size == 0:
            return False, "所有文件大小为 0", False

        if remote_path and shell_fn:
            remote_count, remote_size = _get_remote_aee_stats(shell_fn, remote_path)
            if remote_count is None:
                size_kb = total_size / 1024.0
                return True, f"本地数据存在但未能与设备端比对 (文件数:{file_count}, 大小:{size_kb:.1f}KB)", False
            if remote_count == 0:
                size_kb = total_size / 1024.0
                return True, f"设备端已清理,本地视为最终 (文件数:{file_count}, 大小:{size_kb:.1f}KB)", True
            if file_count < remote_count:
                return False, f"文件数量不完整 (本地:{file_count}, 远程:{remote_count})", True
            if remote_size is not None and remote_size > 0 and total_size < remote_size * 0.9:
                return False, f"文件总大小不匹配 (本地:{total_size}, 远程:{remote_size})", True
            size_kb = total_size / 1024.0
            return True, f"验证通过 (文件数:{file_count}/{remote_count}, 大小:{size_kb:.1f}KB, dbg:{len(dbg_files)})", True

        size_kb = total_size / 1024.0
        return True, f"本地校验通过 (文件数:{file_count}, 大小:{size_kb:.1f}KB, dbg:{len(dbg_files)})", False
    except Exception as exc:
        return False, f"验证过程异常: {exc}", False


def _get_remote_aee_stats(
    shell_fn: ShellFn, remote_path: str
) -> Tuple[Optional[int], Optional[int]]:
    """Return (file_count, total_size_bytes) via shell find+stat; 失败返回 (None, None)。"""
    awk_expr = "{c++;s+=$1}END{print c,s}"
    cmd = (
        "find " + remote_path + " -type f -exec stat -c '%s' {} \\; 2>/dev/null"
        " | awk '" + awk_expr + "'"
    )
    try:
        out = shell_fn(cmd, 15)
    except Exception:
        return None, None
    if not out:
        return None, None
    parts = out.strip().split()
    if not parts:
        return None, None
    count = int(parts[0]) if parts[0].isdigit() else None
    size = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else None
    return count, size


def _write_text(path: Path, content: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        logger.warning("write_text_failed path=%s err=%s", path, exc)


def _load_pending_tasks(state_store: Any, key: str) -> Dict[str, Dict[str, Any]]:
    import json
    raw = state_store.get_state(key, "{}")
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items()}
    except json.JSONDecodeError:
        pass
    return {}


def _save_pending_tasks(state_store: Any, key: str, tasks: Dict[str, Dict[str, Any]]) -> None:
    import json
    state_store.set_state(key, json.dumps(tasks))
