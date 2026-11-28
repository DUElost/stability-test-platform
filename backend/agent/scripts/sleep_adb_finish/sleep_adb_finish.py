# -*- coding: utf-8 -*-
"""宿主侧 ADB 驱动休眠唤醒专项——收尾与结果落盘（teardown 阶段）。

特点：
1. 设备端无 APK 与 prefs 足迹，无需 disarm；
2. 收尾时执行唤醒 + 解锁回桌面，并恢复 ``svc power stayon usb`` 确保设备在可控唤醒态归还；
3. 按与现有 ``sleep_finish`` 兼容的结果格式写入
   ``{STP_AEE_NFS_ROOT}/sleep/{project}/results/{run_id}.json``。

STP_STEP_PARAMS:
{
    "project": "legacy"
}
"""
from __future__ import annotations

import json
import sys
import time
from typing import Any, Callable

from _lib import (
    device_online,
    device_serial,
    is_awake_state,
    job_id,
    load_state,
    output_result,
    params,
    progress_stamp,
    project_name,
    read_wakefulness,
    restore_awake_environment,
    results_dir,
    trigger_wakeup,
)


def _resolve_final_status(state: dict[str, Any]) -> str:
    recorded = str(state.get("final_status") or "").strip().upper()
    if recorded in ("PASS", "FAIL"):
        return recorded
    wake_failures = int(state.get("wake_failures", 0))
    cycles_done = int(state.get("cycles_done", 0))
    expected_cycles = int(state.get("test_times", 0))
    if wake_failures > 0:
        return "FAIL"
    if expected_cycles > 0 and cycles_done >= expected_cycles:
        return "PASS"
    if expected_cycles == 0 and cycles_done > 0:
        return "PASS"
    return "INCOMPLETE"


def _run(
    cfg: dict[str, Any],
    *,
    now_fn: Callable[[], float] = time.time,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    serial = device_serial()
    cur_job_id = job_id()
    state = load_state(expected_job_id=cur_job_id, serial=serial)
    if state is None:
        raise RuntimeError(
            f"未找到本任务（job_id={cur_job_id or '<empty>'}）的宿主侧休眠唤醒状态文件，"
            f"任务可能未经过 sleep_adb_setup 初始化"
        )

    progress_stamp({"seq": 1, "step": "sleep_adb_finish", "phase": "restore_awake"})
    awake_on_finish = False
    if device_online():
        unlock = bool(state.get("unlock_to_home", True))
        trigger_wakeup(unlock=unlock)
        sleep_fn(1.0)
        restore_awake_environment()
        awake_on_finish = is_awake_state(read_wakefulness())

    project = project_name(cfg, state)
    entries = list(state.get("entries") or [])
    cycles_done = int(state.get("cycles_done", 0))
    expected_cycles = int(state.get("test_times", 0))
    wake_failures = int(state.get("wake_failures", 0))
    sleep_anomalies = int(state.get("sleep_anomalies", 0))
    final_status = _resolve_final_status(state)

    ts = time.strftime("%Y%m%d_%H%M%S", time.localtime(now_fn()))
    run_id = f"sleep_adb_{ts}_{serial}"

    metrics: dict[str, Any] = {
        "run_id": run_id,
        "driver": "host_adb",
        "cycles_done": cycles_done,
        "expected_cycles": expected_cycles,
        "wake_failures": wake_failures,
        "sleep_anomalies": sleep_anomalies,
        "final_status": final_status,
        "awake_on_finish": awake_on_finish,
    }
    if state.get("failure_reason"):
        metrics["failure_reason"] = str(state["failure_reason"])

    detail_dir = results_dir(project)
    detail_dir.mkdir(parents=True, exist_ok=True)
    detail_file = detail_dir / f"{run_id}.json"
    detail_payload = {
        "run_id": run_id,
        "driver": "host_adb",
        "metrics": metrics,
        "entries": entries,
    }
    detail_bytes = json.dumps(detail_payload, ensure_ascii=False).encode("utf-8")
    detail_file.write_bytes(detail_bytes)
    metrics["result_bytes"] = len(detail_bytes)

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
