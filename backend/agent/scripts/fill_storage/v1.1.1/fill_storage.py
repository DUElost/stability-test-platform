"""Fill device storage to a target percentage using dd.

v1.1.1（#3085，2026-09-22）：**按目标百分比双向调节**——v1.0.0–v1.1.0 是单向的：
``need = target_used - used_kb``，``need<=0`` 直接 ``skipped+already_met``
**不缩容、不回收自建文件**；且 ``used_kb`` 是 ``df`` 原值，**包含 fill 文件自身**。
后果（2026-09-22 设备满盘取证：24 台慢性 ``pm install: not enough space``）：
按 60% 填过一次后，文件（数十 GB）留在盘上；之后目标改成 40%、或其它数据把
``used`` 推高，都会让 ``need<=0`` 短路 —— 文件永不被缩容/删除，每一步都报
``already_met=True`` 假绿（实测单文件 ``/data/local/tmp/fill.bin`` 45.5 GB）。

v1.1.1 改法（「填到目标百分比」的语义不变，只把单向变双向、把基线算对）：
- ``base_used = used_kb - fill_kb``（**剔除自建文件**后再比目标）；
- ``need = target_used - base_used``：
  - ``need <= 0`` 且无自建文件 → 维持 ``skipped+already_met``；
  - ``need <= 0`` 且有自建文件 → **释放**（``rm -f <fill_path>``，只碰参数指定路径）；
  - ``need > 0`` → ``dd`` 覆盖写到恰好 ``need``（同目标重复运行幂等；目标调低即缩容）；
- metrics 增 ``mode``（``filled``/``released``/``already_met``）与 ``base_pct``，便于跨窗聚合；
- 沿用 v1.0.2 的两处修复（块数向上取整、回读按**绝对量**核验）与 v1.1.0 的
  ``progress_heartbeat``（长 dd 不被 stall_seconds 误杀）。

v1.1.0（#1690）：dd 填盘（最长 300s）全程 PROGRESS 心跳 + 起止戳，启用
stall_seconds 的 Plan 不再把长填盘当停滞误杀；capabilities.json 声明
progress_stamps。

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
    {"success": true/false, "skipped": bool, "error_message": "...",
     "metrics": {"mode": "filled|released|already_met", "filled_kb"|"released_kb": int,
                 "base_pct": int, "actual_pct": int, "already_met": bool}}
"""

import subprocess

from _adb import adb_shell_quiet, device_serial, output_result, params, progress_heartbeat


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


def _fill_file_kb(path: str) -> int:
    """自建 fill 文件当前大小（KB）；不存在/不可读一律按 0 计。

    v1.1.1：基线剔除需要它——``used_kb`` 含该文件，不剔除就会出现
    「自己的文件把自己算成已达标」的假绿。
    """
    try:
        result = adb_shell_quiet(f"du -sk {path}", timeout=30)
    except Exception:  # noqa: BLE001 — 探测失败按 0 计，后续 dd/核验兜住
        return 0
    if result.returncode != 0:
        return 0
    parts = (result.stdout or "").split()
    try:
        return max(int(parts[0]), 0)
    except (IndexError, ValueError):
        return 0


def _pct(part_kb: int, total_kb: int) -> int:
    return part_kb * 100 // total_kb if total_kb else 0


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

    # v1.1.1：基线剔除自建文件后再比目标（v1.1.0 及以前直接用 df 原值 → 假绿）
    fill_kb = _fill_file_kb(fill_path)
    base_used = max(used_kb - fill_kb, 0)
    target_used = total_kb * target_pct // 100
    need_kb = target_used - base_used

    if need_kb <= 0:
        if fill_kb <= 0:
            output_result(True, skipped=True, skip_reason="Storage already at target",
                          metrics={"already_met": True, "mode": "already_met",
                                   "base_pct": _pct(base_used, total_kb),
                                   "current_pct": _pct(used_kb, total_kb)})
            return
        # v1.1.1 释放：真实占用已达/超目标 → 回收自建文件（只碰 fill_path）
        release = adb_shell_quiet(f"rm -f {fill_path}", timeout=30)
        if release.returncode != 0:
            output_result(
                False,
                error_message=(
                    f"release failed: rc={release.returncode} "
                    f"err={(release.stderr or '').strip()[:200]}"
                ),
                metrics={"mode": "released", "released_kb": fill_kb, "base_pct": _pct(base_used, total_kb)},
            )
            return
        try:
            total_after, used_after = _parse_df()
        except Exception as exc:
            output_result(False, error_message=f"post-release df parse failed: {exc}")
            return
        output_result(True, metrics={
            "mode": "released",
            "released_kb": fill_kb,
            "base_pct": _pct(base_used, total_kb),
            "used_after_kb": used_after,
            "actual_pct": _pct(used_after, total_after),
        })
        return

    # #1554：向上取整——floor 会少写至多 block_size_kb-1 KB，把「正好到目标」
    # 压到目标线以下，配合下面的百分比取整就成了必然假失败。
    blocks = max(-(-need_kb // block_size_kb), 1)

    try:
        # #1690：dd 可到 300s——全程 PROGRESS 心跳（stall_seconds 下不被误杀）。
        with progress_heartbeat("dd"):
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
                "mode": "filled",
                "filled_kb": max(used_after - used_kb, 0),
                "need_kb": need_kb,
                "base_used_kb": base_used,
                "used_after_kb": used_after,
                "target_used_kb": target_used,
            },
        )
        return

    output_result(
        True,
        metrics={
            "mode": "filled",
            "filled_kb": max(used_after - used_kb, 0),
            "blocks": blocks,
            "target_pct": target_pct,
            "base_used_kb": base_used,
            "used_after_kb": used_after,
            "target_used_kb": target_used,
            "actual_pct": _pct(used_after, total_after),
        },
    )


if __name__ == "__main__":
    main()
