# -*- coding: utf-8 -*-
"""Sleep 停止 + 结果收取（teardown 阶段，issue #462 P0a；G15 对齐 §3.1）。

移植自 stability_Sleep-Test/scripts/stop.ps1 + lib.ps1（Stop-SleepTestTask）。
PC wake-watchdog 不移植（G15 决策：OEM 闹钟丢失场景记已知缺口，patrol 兜底）。

流程：
1. 停任务：prefs auto_resume=false+running=false → SLEEP_TEST_STOP 优雅停止 → force-stop 兜底
2. 拉取 sleep_test_result.txt（主路径 /sdcard/Android/data/.../files/SleepTest/，旧路径兜底）
3. 解析（parse_sleep_result）→ 摘要 metrics
4. 逐行结果写 {STP_AEE_NFS_ROOT}/sleep/{project}/results/{run_id}.json
   （run_id = 收尾时刻 sleep_YYYYmmdd_HHMMSS）
5. stdout JSON 只带摘要（step_trace 64KiB 截断约束同 MTBF）

STP_STEP_PARAMS:
{
    "project": "legacy",
    "force_stop": true      // 优雅停止失败时强制杀（stop.bat -Force 语义）
}

输出 (stdout): {"success": true/false, "metrics": {...}, "detail_uri": "..."}
metrics: {run_id, cycles_done, expected_cycles, wake_failures, sleep_anomalies,
          final_status, result_bytes}
final_status: PASS | FAIL | INCOMPLETE（无 finished 行 = 测试未收尾）
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
    device_serial,
    output_result,
    param_or_env,
    params,
    parse_size_from_ls,
    parse_sleep_result,
    results_dir,
    result_paths,
    stop_task,
)


def _pull_result_file() -> Path:
    """拉取结果文件（主路径优先，旧路径兜底）；都不存在则报错。"""
    for path in result_paths():
        ls = adb_shell(f"ls -l {path}", timeout=30).strip()
        if parse_size_from_ls(ls) <= 0:
            continue
        local = Path(tempfile.mkdtemp(prefix="sleep-results-")) / Path(path).name
        rc, _, err = adb("pull", path, str(local), timeout=120)
        if rc != 0:
            raise RuntimeError(f"adb pull 失败 {path}: {err.strip() or 'rc=%d' % rc}")
        if local.is_file():
            return local
    raise RuntimeError("设备端没有 sleep_test_result.txt（主路径与旧路径均不存在），任务可能未真正运行")


def _run(cfg: dict) -> dict:
    project = str(param_or_env(cfg, "project", "STP_SLEEP_PROJECT", "legacy"))
    force = str(param_or_env(cfg, "force_stop", "STP_SLEEP_FORCE_STOP", "true")).lower() == "true"

    stop_task(force=force)
    time.sleep(2)   # 等结果文件收尾（服务停止时 flush）

    local_file = _pull_result_file()
    parsed = parse_sleep_result(local_file.read_bytes())
    run_id = time.strftime("sleep_%Y%m%d_%H%M%S")
    final_status = parsed["final_status"] or "INCOMPLETE"
    metrics = {
        "run_id": run_id,
        "cycles_done": parsed["cycles_done"],
        "expected_cycles": parsed["expected_cycles"],
        "wake_failures": parsed["wake_failures"],
        "sleep_anomalies": parsed["sleep_anomalies"],
        "final_status": final_status,
        "result_bytes": local_file.stat().st_size,
    }

    # 逐行结果写中心存储（P2 test_case_result 数据源，mtbf results/ 同款）
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
