"""Fill device storage to a target percentage using dd.

v1.0.1（#812）：dd 返回码必须检查，且完成后回读 df 核验达到 target——
ext4 保留块/并发写入可能让 dd 提前 ENOSPC 退出，原实现仍报成功并虚报
filled_kb。

Environment:
    STP_DEVICE_SERIAL   (required)
    STP_ADB_PATH        (default: adb)
    STP_STEP_PARAMS     (optional, JSON: {target_percentage: int, block_size_kb: int, fill_path: str})

Output (stdout):
    {"success": true/false, "skipped": bool, "error_message": "...", "metrics": {"filled_kb": int, "already_met": bool}}
"""

import subprocess

from _adb import adb_shell_quiet, device_serial, output_result, params


def _parse_df() -> tuple[int, int]:
    result = adb_shell_quiet("df /data", timeout=10)
    if result.returncode != 0:
        raise ValueError(f"df failed: rc={result.returncode}")
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
    device_serial()  # 校验 STP_DEVICE_SERIAL 是否存在（缺失即退出）
    args = params()

    target_pct = args.get("target_percentage", 60)
    block_size_kb = args.get("block_size_kb", 1024)
    fill_path = args.get("fill_path", "/data/local/tmp/fill.bin")

    try:
        total_kb, used_kb = _parse_df()
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
        fill = adb_shell_quiet(
            f"dd if=/dev/zero of={fill_path} bs={block_size_kb}k count={blocks}",
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        output_result(False, error_message="Storage fill timed out")
        return
    except Exception as exc:
        output_result(False, error_message=f"Storage fill failed: {exc}")
        return

    if fill.returncode != 0:
        output_result(
            False,
            error_message=f"dd failed: rc={fill.returncode} err={(fill.stderr or '').strip()[:200]}",
        )
        return

    # 回读 df 核验达到 target（dd 成功退出 ≠ 写满：保留块/并发写入可留缺口）
    try:
        total_after, used_after = _parse_df()
    except Exception as exc:
        output_result(False, error_message=f"post-fill df parse failed: {exc}")
        return

    actual_pct = used_after * 100 // total_after if total_after else 0
    if actual_pct < target_pct:
        output_result(
            False,
            error_message=f"fill insufficient: {actual_pct}% < target {target_pct}%",
            metrics={"filled_kb": need_kb, "actual_pct": actual_pct},
        )
        return

    output_result(
        True,
        metrics={
            "filled_kb": need_kb,
            "blocks": blocks,
            "target_pct": target_pct,
            "actual_pct": actual_pct,
        },
    )


if __name__ == "__main__":
    main()
