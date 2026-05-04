"""Fill device storage to a target percentage using dd.

Environment:
    STP_DEVICE_SERIAL   (required)
    STP_ADB_PATH        (default: adb)
    STP_STEP_PARAMS     (optional, JSON: {target_percentage: int, block_size_kb: int, fill_path: str})

Output (stdout):
    {"success": true/false, "skipped": bool, "error_message": "...", "metrics": {"filled_kb": int, "already_met": bool}}
"""

import subprocess
import sys
from _adb import adb_path, adb_shell, device_serial, output_result, params


def _parse_df(serial: str) -> tuple[int, int]:
    result = subprocess.run(
        [adb_path(), "-s", serial, "shell", "df /data"],
        capture_output=True, text=True, timeout=10,
    )
    lines = (result.stdout or "").strip().splitlines()
    if len(lines) < 2:
        raise ValueError("Cannot parse df output: not enough lines")
    parts = lines[1].split()
    if len(parts) < 4:
        raise ValueError("Cannot parse df columns")
    total_kb = int(parts[1])
    used_kb = int(parts[2])
    return total_kb, used_kb


def main() -> None:
    serial = device_serial()
    args = params()

    target_pct = args.get("target_percentage", 60)
    block_size_kb = args.get("block_size_kb", 1024)
    fill_path = args.get("fill_path", "/data/local/tmp/fill.bin")

    try:
        total_kb, used_kb = _parse_df(serial)
    except Exception as exc:
        output_result(False, error_message=f"df parse failed: {exc}")
        return

    target_used = total_kb * target_pct // 100
    need_kb = target_used - used_kb

    if need_kb <= 0:
        output_result(True, skipped=True, skip_reason="Storage already at target",
                      metrics={"already_met": True, "current_pct": used_kb * 100 // total_kb})
        return

    blocks = max(need_kb // block_size_kb, 1)

    try:
        subprocess.run(
            [adb_path(), "-s", serial, "shell",
             f"dd if=/dev/zero of={fill_path} bs={block_size_kb}k count={blocks}"],
            capture_output=True, text=True, timeout=300,
        )
        output_result(True, metrics={"filled_kb": need_kb, "blocks": blocks, "target_pct": target_pct})
    except subprocess.TimeoutExpired:
        output_result(False, error_message="Storage fill timed out")
    except Exception as exc:
        output_result(False, error_message=f"Storage fill failed: {exc}")


if __name__ == "__main__":
    main()
