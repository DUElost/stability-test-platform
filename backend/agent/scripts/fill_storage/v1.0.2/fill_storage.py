"""Fill device storage to a target percentage using dd.

v1.0.2（#1554）：修正回读核验的假失败——v1.0.1 引入的校验叠加两处向下取整，
使「正好灌到目标」几乎必然算成 target-1 个百分点：

- ``blocks = need_kb // block_size_kb``（floor）少写至多 block_size_kb-1 KB；
- ``actual_pct = used_after * 100 // total_after``（floor）再截一次。

以 total_kb=119473921 / target=60% 为例：target_used=71684352，dd 实写
70004*1024=71684096 KB，回读 71684096*100//119473921 = 59 → 误报
``fill insufficient: 59% < target 60%``。要判成功需 target_pct*total 恰好整除
**且** need_kb 恰好是 block_size_kb 的整数倍，真实 /data 容量几乎不满足。

v1.0.2 改法：块数向上取整；核验改为整数**绝对量**比较（used_after >= target_used），
不再重算一个被截断的百分比。

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

    # #1554：向上取整——floor 会少写至多 block_size_kb-1 KB，把「正好到目标」
    # 压到目标线以下，配合下面的百分比取整就成了必然假失败。
    blocks = max(-(-need_kb // block_size_kb), 1)

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

    # #1554：用绝对量比较，不重算被截断的百分比。
    if used_after < target_used:
        output_result(
            False,
            error_message=(
                f"fill insufficient: used {used_after}KB < target {target_used}KB "
                f"({target_pct}% of {total_after}KB)"
            ),
            metrics={
                "filled_kb": max(used_after - used_kb, 0),
                "need_kb": need_kb,
                "used_after_kb": used_after,
                "target_used_kb": target_used,
            },
        )
        return

    output_result(
        True,
        metrics={
            "filled_kb": max(used_after - used_kb, 0),
            "blocks": blocks,
            "target_pct": target_pct,
            "used_after_kb": used_after,
            "target_used_kb": target_used,
            "actual_pct": used_after * 100 // total_after if total_after else 0,
        },
    )


if __name__ == "__main__":
    main()
