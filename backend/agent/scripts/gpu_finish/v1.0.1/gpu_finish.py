# -*- coding: utf-8 -*-
"""GPU 停止 + 结果收取（teardown 阶段，issue #462 P0c；G15 对齐 §3.3）。

移植自 stability_GPU-Test/stop.bat（force-stop 4 包 + pkill instrument；bat 注释：
压测跑在 Antutu 进程内，只停框架）。

流程：
1. 停任务：force-stop 4 包 + pkill 循环脚本/instrument
2. 拉取 /sdcard/Auto/test_log.txt（instrument stdout + 平台标记行原文）
3. 解析（parse_gpu_log）→ 摘要 metrics（标记行为准；instrument 输出原文备查）
4. 摘要 JSON 写 {STP_AEE_NFS_ROOT}/gpu/{project}/results/{run_id}.json
   （run_id = 收尾时刻 gpu_YYYYmmdd_HHMMSS）
5. stdout JSON 只带摘要（step_trace 64KiB 截断约束同 MTBF）

STP_STEP_PARAMS:
{
    "project": "legacy"
}

输出 (stdout): {"success": true/false, "metrics": {...}, "detail_uri": "..."}
metrics: {run_id, rounds_done, expected_rounds, failed_rounds, end_rc, final_status, log_bytes}
final_status: COMPLETED（有 GPU_RUN_END）| INCOMPLETE（无 END 标记）
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

from _lib import (
    _RESULT_LOG,
    adb,
    output_result,
    param_or_env,
    params,
    parse_gpu_log,
    result_log_bytes,
    results_dir,
    stop_stress,
)


def _pull_result_log() -> Path:
    """拉取 test_log.txt 到本地临时目录；不存在则报错。"""
    if result_log_bytes() <= 0:
        raise RuntimeError(f"设备端没有 {_RESULT_LOG}，任务可能未真正运行")
    local = Path(tempfile.mkdtemp(prefix="gpu-results-")) / "test_log.txt"
    rc, _, err = adb("pull", _RESULT_LOG, str(local), timeout=300)
    if rc != 0 or not local.is_file():
        raise RuntimeError(f"adb pull {_RESULT_LOG} 失败: {err.strip() or 'rc=%d' % rc}")
    return local


def _run(cfg: dict) -> dict:
    project = str(param_or_env(cfg, "project", "STP_GPU_PROJECT", "legacy"))

    stop_stress()
    time.sleep(2)   # 等 instrument 退出、log 收尾

    local_file = _pull_result_log()
    parsed = parse_gpu_log(local_file.read_text(encoding="utf-8", errors="replace"))
    run_id = time.strftime("gpu_%Y%m%d_%H%M%S")
    final_status = "COMPLETED" if parsed["end_rc"] is not None else "INCOMPLETE"
    metrics = {
        "run_id": run_id,
        "test_id": parsed["test_id"],
        "rounds_done": parsed["rounds_done"],
        "expected_rounds": parsed["expected_rounds"],
        "failed_rounds": parsed["failed_rounds"],
        "end_rc": parsed["end_rc"],
        "final_status": final_status,
        "log_bytes": local_file.stat().st_size,
    }

    detail_dir = results_dir(project)
    detail_dir.mkdir(parents=True, exist_ok=True)
    detail_file = detail_dir / f"{run_id}.json"
    detail_file.write_text(
        json.dumps({"run_id": run_id, "metrics": metrics, "rounds": parsed["rounds"]}, ensure_ascii=False),
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
