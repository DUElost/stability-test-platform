"""File transfer pipeline actions: adb_pull, collect_bugreport, scan_aee, export_mobilelogs."""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from ..pipeline_engine import StepContext, StepResult

logger = logging.getLogger(__name__)


def adb_pull(ctx: StepContext) -> StepResult:
    """Pull files/directories from device to host."""
    remote_path = ctx.params.get("remote_path", "")
    local_path = ctx.params.get("local_path", "/tmp/")

    if not remote_path:
        return StepResult(success=False, exit_code=1, error_message="No remote_path specified")

    os.makedirs(local_path, exist_ok=True)

    try:
        ctx.adb.pull(ctx.serial, remote_path, local_path)
        if ctx.logger:
            ctx.logger.info(f"Pulled {remote_path} -> {local_path}")
        return StepResult(success=True, metrics={"remote_path": remote_path, "local_path": local_path})
    except Exception as e:
        return StepResult(success=False, exit_code=1, error_message=f"adb pull failed: {e}")


def collect_bugreport(ctx: StepContext) -> StepResult:
    """Generate a bugreport on device and pull it to host."""
    remote_path = ctx.params.get("remote_path", "/sdcard/bugreport.txt")
    local_dir = ctx.params.get("local_dir", "/tmp/")

    os.makedirs(local_dir, exist_ok=True)
    local_path = os.path.join(local_dir, "bugreport.txt")

    try:
        if ctx.logger:
            ctx.logger.info("Generating bugreport (this may take a while)...")
        ctx.adb.shell(ctx.serial, f"bugreport {remote_path}", timeout=300)
        ctx.adb.pull(ctx.serial, remote_path, local_path)
        if ctx.logger:
            ctx.logger.info(f"Bugreport saved to {local_path}")
        return StepResult(success=True, metrics={"local_path": local_path})
    except Exception as e:
        return StepResult(success=False, exit_code=1, error_message=f"Bugreport failed: {e}")


def _stdout(result) -> str:
    """Extract stdout string from subprocess.CompletedProcess or plain string."""
    if hasattr(result, "stdout"):
        return result.stdout or ""
    return str(result) if result is not None else ""


def _load_whitelist(path: str) -> set:
    """Load whitelist file (one package name per line)."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return {line.strip() for line in f if line.strip()}
    except Exception:
        return set()


def _parse_db_history_line(line: str):
    """Parse a single db_history line (comma-separated).

    Original script field mapping (MonkeyAEEinfo_Stability_20250901.py:1233):
      col 0 = db_path   (device-side absolute path, e.g. /data/aee_exp/db.01.NE)
      col 8 = pkg_name  (process/package name, e.g. com.android.systemui)
      col 9 = timestamp  (e.g. "Sat Jul 19 10:15:42 CST 2025 @ 2025-07-19 11:28:43.273301")

    Returns (db_path, pkg_name, timestamp_str) or None if malformed.
    """
    fields = line.strip().split(",")
    if len(fields) < 10:
        return None
    return fields[0].strip(), fields[8].strip(), fields[9].strip()


def scan_aee(ctx: StepContext) -> StepResult:
    """Scan AEE directories on device and pull exception entries to host.

    When incremental=true, uses LocalDB to track processed entries across runs,
    only pulling new entries. Falls back to full mode if local_db is None.
    """
    aee_dirs = ctx.params.get("aee_dirs", ["/data/aee_exp", "/data/vendor/aee_exp"])
    local_dir = ctx.params.get("local_dir", "/tmp/aee")
    incremental = ctx.params.get("incremental", False)
    whitelist_file = ctx.params.get("whitelist_file", "")
    state_key_prefix = ctx.params.get("state_key_prefix", "scan_aee")

    os.makedirs(local_dir, exist_ok=True)

    # Fallback to full mode if incremental requested but no local_db
    if incremental and (ctx.local_db is None or not hasattr(ctx.local_db, "get_state")):
        if ctx.logger:
            ctx.logger.warn("incremental=true but local_db unavailable, falling back to full mode")
        incremental = False

    if not incremental:
        return _scan_aee_full(ctx, aee_dirs, local_dir)

    return _scan_aee_incremental(ctx, aee_dirs, local_dir, whitelist_file, state_key_prefix)


def _scan_aee_full(ctx: StepContext, aee_dirs: list, local_dir: str) -> StepResult:
    """Original full-pull scan_aee logic."""
    total_scanned = 0
    total_pulled = 0
    errors = []

    for aee_dir in aee_dirs:
        try:
            ls_output = ctx.adb.shell(ctx.serial, f"ls -1 {aee_dir}/ 2>/dev/null", timeout=10)
            entries = [e.strip() for e in _stdout(ls_output).strip().splitlines() if e.strip()]
            total_scanned += len(entries)

            for entry in entries:
                remote_entry = f"{aee_dir}/{entry}"
                local_entry = os.path.join(local_dir, entry)
                try:
                    os.makedirs(local_entry, exist_ok=True)
                    ctx.adb.pull(ctx.serial, remote_entry, local_entry)
                    total_pulled += 1
                except Exception as e:
                    errors.append(f"Failed to pull {remote_entry}: {e}")
        except Exception as e:
            logger.debug(f"Cannot list {aee_dir}: {e}")

    if ctx.logger:
        ctx.logger.info(f"AEE scan (full): {total_scanned} entries found, {total_pulled} pulled")
        if errors:
            ctx.logger.warn(f"AEE pull errors: {len(errors)}")

    return StepResult(
        success=True,
        metrics={"scanned": total_scanned, "pulled": total_pulled, "errors": len(errors)},
    )


def _scan_aee_incremental(
    ctx: StepContext, aee_dirs: list, local_dir: str, whitelist_file: str, prefix: str
) -> StepResult:
    """Incremental scan: read db_history, diff against LocalDB, pull only new entries."""
    total_scanned = 0
    total_pulled = 0
    skipped_known = 0
    filtered_whitelist = 0
    pull_errors = 0
    new_timestamps = []

    # Load whitelist (cached in shared)
    whitelist = None
    if whitelist_file:
        if "_whitelist" in ctx.shared:
            whitelist = ctx.shared["_whitelist"]
        else:
            whitelist = _load_whitelist(whitelist_file)
            ctx.shared["_whitelist"] = whitelist
            if ctx.logger:
                ctx.logger.info(f"Loaded whitelist: {len(whitelist)} packages from {whitelist_file}")

    for aee_dir in aee_dirs:
        aee_type = "vendor_aee_exp" if "vendor" in aee_dir else "aee_exp"
        state_key = f"{prefix}:{ctx.serial}:{aee_type}:processed_entries"

        # 1. Load processed set from LocalDB
        try:
            raw = ctx.local_db.get_state(state_key, "[]")
            processed_set = set(json.loads(raw))
        except Exception:
            processed_set = set()

        # 2. Read db_history from device
        try:
            history_output = ctx.adb.shell(ctx.serial, f"cat {aee_dir}/db_history 2>/dev/null", timeout=30)
            history_lines = _stdout(history_output).strip().splitlines()
        except Exception as e:
            logger.debug(f"Cannot read {aee_dir}/db_history: {e}")
            continue

        if not history_lines:
            continue

        # 3. Parse entries
        current_entries = {}  # line_key -> (db_path, pkg_name, timestamp_str)
        for line in history_lines:
            parsed = _parse_db_history_line(line)
            if parsed is None:
                logger.warning(f"Skipping malformed db_history line: {line[:100]}")
                continue
            db_path, pkg_name, timestamp_str = parsed
            line_key = line.strip()
            current_entries[line_key] = (db_path, pkg_name, timestamp_str)

        total_scanned += len(current_entries)

        # 4. Whitelist filter (only for aee_exp, not vendor_aee_exp)
        if whitelist and aee_type == "aee_exp":
            before = len(current_entries)
            current_entries = {
                k: v for k, v in current_entries.items() if v[1] in whitelist
            }
            filtered_whitelist += before - len(current_entries)

        # 5. Diff
        current_keys = set(current_entries.keys())
        new_keys = current_keys - processed_set

        skipped_known += len(current_keys) - len(new_keys)

        # 6. Pull new entries
        for key in new_keys:
            db_path, pkg_name, timestamp_str = current_entries[key]
            # Build local target path
            safe_name = db_path.replace("/", "_").strip("_")
            local_target = os.path.join(local_dir, aee_type, safe_name)
            try:
                os.makedirs(os.path.dirname(local_target), exist_ok=True)
                ctx.adb.pull(ctx.serial, db_path, local_target)
                total_pulled += 1
                new_timestamps.append(timestamp_str)
            except Exception as e:
                pull_errors += 1
                if ctx.logger:
                    ctx.logger.warn(f"Failed to pull {db_path}: {e}")

        # 7. Update processed set in LocalDB
        try:
            ctx.local_db.set_state(state_key, json.dumps(list(current_keys | processed_set)))
        except Exception as e:
            if ctx.logger:
                ctx.logger.warn(f"Failed to save incremental state: {e}")

    if ctx.logger:
        ctx.logger.info(
            f"AEE scan (incremental): scanned={total_scanned}, pulled={total_pulled}, "
            f"skipped_known={skipped_known}, filtered={filtered_whitelist}, errors={pull_errors}"
        )

    return StepResult(
        success=True,
        metrics={
            "scanned": total_scanned,
            "pulled": total_pulled,
            "skipped_known": skipped_known,
            "filtered_whitelist": filtered_whitelist,
            "new_timestamps": new_timestamps,
            "errors": pull_errors,
        },
    )


def _parse_mobilelog_dirname(name: str):
    """Parse datetime from mobilelog directory name.

    Supports: APLog_YYYY_MMDD_HHMMSS and APLog_YYYY_MM_DD_HH_MM_SS
    Returns datetime or None.
    """
    import re

    # APLog_2026_0304_153022
    m = re.match(r"APLog_(\d{4})_(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", name)
    if m:
        try:
            return datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]), int(m[6]))
        except ValueError:
            return None

    # APLog_2026_03_04_15_30_22
    m = re.match(r"APLog_(\d{4})_(\d{2})_(\d{2})_(\d{2})_(\d{2})_(\d{2})", name)
    if m:
        try:
            return datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]), int(m[6]))
        except ValueError:
            return None

    return None


def _parse_aee_timestamp(ts_str: str):
    """Parse AEE timestamp from db_history (col 9).

    Supports formats found in real device data:
    - "Sat Jul 19 10:15:42 CST 2025 @ 2025-07-19 11:28:43.273301" (@ separator → use RHS)
    - "2025-07-19 11:28:43.273301" (ISO with microseconds)
    - "2025-07-19 11:28:43" (ISO without microseconds)
    - "2025/07/19 11:28:43"
    - "2025-07-19T11:28:43"
    - Epoch seconds (integer)

    Returns datetime or None.
    """
    ts_str = ts_str.strip()

    # Handle @ separator: "ctime_part @ iso_part" → prefer the iso part after @
    if " @ " in ts_str:
        ts_str = ts_str.split(" @ ", 1)[1].strip()

    # Try epoch (integer seconds)
    try:
        epoch = int(ts_str)
        return datetime.utcfromtimestamp(epoch)
    except (ValueError, OSError):
        pass

    # Try known strptime formats (most specific first)
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",    # 2025-07-19 11:28:43.273301
        "%Y-%m-%d %H:%M:%S",        # 2025-07-19 11:28:43
        "%Y-%m-%dT%H:%M:%S",        # 2025-07-19T11:28:43
        "%Y/%m/%d %H:%M:%S",        # 2025/07/19 11:28:43
    ):
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue

    # Try BSD/ctime format: "Sat Jul 19 10:15:42 CST 2025"
    # Strip timezone abbreviation (CST etc.) since strptime can't reliably parse it
    import re
    m = re.match(
        r"\w{3}\s+(\w{3})\s+(\d{1,2})\s+(\d{2}:\d{2}:\d{2})\s+\w+\s+(\d{4})",
        ts_str,
    )
    if m:
        month_str, day, time_str, year = m.group(1), m.group(2), m.group(3), m.group(4)
        try:
            return datetime.strptime(f"{month_str} {day} {time_str} {year}", "%b %d %H:%M:%S %Y")
        except ValueError:
            pass

    return None


def export_mobilelogs(ctx: StepContext) -> StepResult:
    """Pull mobilelog directories correlated with AEE timestamps."""
    timestamps_from_step = ctx.params.get("timestamps_from_step", "")
    mobilelog_path = ctx.params.get("mobilelog_path", "/data/debuglogger/mobilelog/")
    local_dir = ctx.params.get("local_dir", "")
    time_window_minutes = ctx.params.get("time_window_minutes", 30)

    if not local_dir:
        return StepResult(success=False, exit_code=1, error_message="No local_dir specified")

    # 1. Get timestamps from shared
    timestamps = []
    if timestamps_from_step and timestamps_from_step in ctx.shared:
        timestamps = ctx.shared[timestamps_from_step].get("new_timestamps", [])

    if not timestamps:
        if ctx.logger:
            ctx.logger.info("No new AEE timestamps, skipping mobilelog export")
        return StepResult(success=True, metrics={"matched": 0, "pulled": 0, "unmatched_timestamps": []})

    os.makedirs(local_dir, exist_ok=True)

    # 2. List mobilelog directories
    try:
        ls_output = ctx.adb.shell(ctx.serial, f"ls -1 {mobilelog_path} 2>/dev/null", timeout=10)
        dir_names = [d.strip() for d in _stdout(ls_output).strip().splitlines() if d.strip()]
    except Exception as e:
        if ctx.logger:
            ctx.logger.warn(f"Cannot list {mobilelog_path}: {e}")
        return StepResult(
            success=True,
            metrics={"matched": 0, "pulled": 0, "unmatched_timestamps": timestamps},
        )

    # Parse all mobilelog directory timestamps
    mobilelog_entries = []  # [(dirname, datetime)]
    for d in dir_names:
        dt = _parse_mobilelog_dirname(d)
        if dt:
            mobilelog_entries.append((d, dt))

    # 3. Match each AEE timestamp to nearest mobilelog dir
    window = timedelta(minutes=time_window_minutes)
    matched = 0
    pulled = 0
    unmatched = []

    for ts_str in timestamps:
        aee_dt = _parse_aee_timestamp(ts_str)
        if aee_dt is None:
            unmatched.append(ts_str)
            continue

        # Find closest mobilelog dir within window
        best_dir = None
        best_delta = None
        for dirname, ml_dt in mobilelog_entries:
            delta = abs(aee_dt - ml_dt)
            if delta <= window and (best_delta is None or delta < best_delta):
                best_dir = dirname
                best_delta = delta

        if best_dir:
            matched += 1
            remote = f"{mobilelog_path.rstrip('/')}/{best_dir}"
            local_target = os.path.join(local_dir, best_dir)
            try:
                os.makedirs(local_target, exist_ok=True)
                ctx.adb.pull(ctx.serial, remote, local_target)
                pulled += 1
                if ctx.logger:
                    ctx.logger.info(f"Pulled mobilelog {best_dir} (matched AEE ts={ts_str})")
            except Exception as e:
                if ctx.logger:
                    ctx.logger.warn(f"Failed to pull mobilelog {best_dir}: {e}")
        else:
            unmatched.append(ts_str)

    if ctx.logger:
        ctx.logger.info(f"Mobilelog export: matched={matched}, pulled={pulled}, unmatched={len(unmatched)}")

    return StepResult(
        success=True,
        metrics={"matched": matched, "pulled": pulled, "unmatched_timestamps": unmatched},
    )
