# -*- coding: utf-8 -*-
"""PowerCycle 停止 + 结果收取（teardown 阶段，issue #462 P0b；G15 对齐 §3.2）。

移植自 stability_PowerCycle-Test/scripts/stop.ps1 + lib.ps1（Stop-PowerCycleTask，
AutoTestTool 后端）。PC pc-watchdog / MSSV 收尾不移植（G15 D3/D4）。

流程：
0. **等待设备上线（v1.0.1，综合验收发现⑦）**：PowerCycle 设备每 ~75s 重启一次，
   teardown 撞上重启窗口会 adb device not found → 结果收集失败（验收 0/4）。
   收取前置 = 设备稳定在线（轮询 get-state，最长 wait_device_online_seconds）。
1. 停任务：prefs auto_resume=false+running=false → POWER_CYCLE_STOP 优雅停止 → force-stop 兜底
2. 拉取 powercycle_result.txt（主路径 /sdcard/Android/data/.../files/PowerCycle/，旧路径兜底）
3. 解析（parse_powercycle_result）→ 摘要 metrics
4. 逐行结果写 {STP_AEE_NFS_ROOT}/power-cycle/{project}/results/{run_id}.json
   （run_id = 收尾时刻 powercycle_YYYYmmdd_HHMMSS_<serial>——v1.0.1 加设备维度，
   多设备并行同秒不再互相覆盖，发现⑨）
5. stdout JSON 只带摘要（step_trace 64KiB 截断约束同 MTBF）

STP_STEP_PARAMS:
{
    "project": "legacy",
    "force_stop": true,                // 优雅停止失败时强制杀（stop.bat -Force 语义）
    "wait_device_online_seconds": 600  // 收取前置：等待设备上线的最大秒数（默认 600）
}

输出 (stdout): {"success": true/false, "metrics": {...}, "detail_uri": "..."}
metrics: {run_id, cycles_done, expected_cycles, reboot_failures, final_status, result_bytes}
final_status: PASS | INCOMPLETE（无 finished 行 = 测试未收尾）
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

from _lib import (
    adb,
    adb_shell,
    device_online,
    device_serial,
    output_result,
    param_or_env,
    params,
    parse_powercycle_result,
    parse_size_from_ls,
    result_paths,
    results_dir,
    stop_task,
)

_DEFAULT_WAIT_ONLINE = 600   # 默认等待设备上线秒数（收取前置）


def _wait_device_online(timeout_s: int) -> bool:
    """轮询设备上线（get-state == device），最长 timeout_s 秒。

    PowerCycle 设备持续重启——收取动作必须在设备在线窗口内执行，
    否则 adb 命令全部失败（验收发现⑦）。
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if device_online():
            return True
        time.sleep(10)
    return False


def _pull_result_file() -> Path:
    """拉取结果文件（主路径优先，旧路径兜底）；都不存在则报错。"""
    for path in result_paths():
        ls = adb_shell(f"ls -l {path}", timeout=30).strip()
        if parse_size_from_ls(ls) <= 0:
            continue
        local = Path(tempfile.mkdtemp(prefix="powercycle-results-")) / Path(path).name
        rc, _, err = adb("pull", path, str(local), timeout=120)
        if rc != 0:
            raise RuntimeError(f"adb pull 失败 {path}: {err.strip() or 'rc=%d' % rc}")
        if local.is_file():
            return local
    raise RuntimeError("设备端没有 powercycle_result.txt（主路径与旧路径均不存在），任务可能未真正运行")


def _run(cfg: dict) -> dict:
    project = str(param_or_env(cfg, "project", "STP_POWER_CYCLE_PROJECT", "legacy"))
    force = str(param_or_env(cfg, "force_stop", "STP_POWER_CYCLE_FORCE_STOP", "true")).lower() == "true"
    wait_online = int(str(param_or_env(
        cfg, "wait_device_online_seconds", "STP_POWER_CYCLE_WAIT_ONLINE_SECONDS", _DEFAULT_WAIT_ONLINE,
    )) or _DEFAULT_WAIT_ONLINE)

    serial = device_serial()
    if not _wait_device_online(wait_online):
        raise RuntimeError(
            f"设备 {serial} 在 {wait_online}s 内未上线（持续重启/离线），无法收取结果——"
            f"请提高 wait_device_online_seconds 或核对设备状态"
        )

    stop_task(force=force)
    time.sleep(2)   # 等结果文件收尾（服务停止时 flush）

    local_file = _pull_result_file()
    parsed = parse_powercycle_result(local_file.read_bytes())
    run_id = f"powercycle_{time.strftime('%Y%m%d_%H%M%S')}_{serial}"
    final_status = parsed["final_status"] or "INCOMPLETE"
    metrics = {
        "run_id": run_id,
        "cycles_done": parsed["cycles_done"],
        "expected_cycles": parsed["expected_cycles"],
        "reboot_failures": parsed["reboot_failures"],
        "final_status": final_status,
        "result_bytes": local_file.stat().st_size,
    }

    # 逐行结果写中心存储（P2 test_case_result 数据源，mtbf/sleep results/ 同款）
    detail_dir = results_dir(project)
    detail_dir.mkdir(parents=True, exist_ok=True)
    detail_file = detail_dir / f"{run_id}.json"
    detail_file.write_text(
        json.dumps({"run_id": run_id, "metrics": metrics, "entries": parsed["entries"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    return {"metrics": metrics, "detail_uri": str(detail_file)}


def main() -> None:
    cfg = params()
    try:
        result = _run(cfg)
    except Exception as exc:  # noqa: BLE001
        output_result(False, error_message=str(exc))
        sys.exit(1)
    output_result(True, **result)


if __name__ == "__main__":
    main()
