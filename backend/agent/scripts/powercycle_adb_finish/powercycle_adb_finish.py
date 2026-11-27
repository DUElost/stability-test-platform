# -*- coding: utf-8 -*-
"""宿主侧 ADB 驱动开关机专项——收尾与结果落盘（teardown 阶段）。

特点：
1. 设备端无自启服务与 prefs 足迹，无需 disarm；
2. 若任务在人工 abort / Plan 超时瞬间恰逢最后一次重启在途，可选等待设备回到
   ``boot_completed == 1`` 并解锁回桌面（``wait_device_online_seconds``），确保
   设备以健康在线态归还给后续任务；即使等待超时也照常落盘宿主侧结果；
3. 将汇总 metrics 与逐轮明细写入
   ``{STP_AEE_NFS_ROOT}/power-cycle/{project}/results/{run_id}.json``，
   与现有 ``powercycle_finish`` 的结果文件结构保持兼容。

STP_STEP_PARAMS:
{
    "project": "legacy",
    "wait_device_online_seconds": 180
}
"""
from __future__ import annotations

import json
import sys
import time
from typing import Any, Callable

from _lib import (
    _DEFAULT_FINISH_WAIT_ONLINE_SECONDS,
    device_serial,
    job_id,
    load_state,
    output_result,
    param_or_env,
    params,
    parse_int,
    progress_stamp,
    project_name,
    results_dir,
    unlock_to_home,
    wait_system_ready,
)


def _compute_boot_stats(entries: list[dict[str, Any]]) -> tuple[float, float]:
    durations = [
        float(item["boot_duration_s"])
        for item in entries
        if item.get("status") == "PASS" and item.get("boot_duration_s") is not None
    ]
    if not durations:
        return 0.0, 0.0
    return round(sum(durations) / len(durations), 2), round(max(durations), 2)


def _resolve_final_status(state: dict[str, Any]) -> str:
    recorded = str(state.get("final_status") or "").strip().upper()
    if recorded in ("PASS", "FAIL"):
        return recorded
    reboot_failures = int(state.get("reboot_failures", 0))
    cycles_done = int(state.get("cycles_done", 0))
    expected_cycles = int(state.get("test_times", 0))
    if reboot_failures > 0:
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
            f"未找到本任务（job_id={cur_job_id or '<empty>'}）的宿主侧开关机状态文件，"
            f"任务可能未经过 powercycle_adb_setup 初始化"
        )

    wait_online_s = parse_int(
        param_or_env(
            cfg,
            "wait_device_online_seconds",
            "STP_POWER_CYCLE_FINISH_WAIT_ONLINE_SECONDS",
            _DEFAULT_FINISH_WAIT_ONLINE_SECONDS,
        ),
        name="wait_device_online_seconds",
        minimum=0,
    )

    device_online_on_finish = True
    if wait_online_s > 0:
        progress_stamp(
            {"seq": 1, "step": "powercycle_adb_finish", "phase": "wait_online", "budget_s": wait_online_s}
        )
        ready, _ = wait_system_ready(
            wait_online_s,
            now_fn=now_fn,
            sleep_fn=sleep_fn,
            on_poll=lambda seq, obs: progress_stamp(
                {"seq": seq + 1, "step": "powercycle_adb_finish", "phase": "wait_online", "observed": obs}
            ),
        )
        device_online_on_finish = ready
        if ready and bool(state.get("unlock_to_home", True)):
            unlock_to_home()

    project = project_name(cfg, state)
    entries = list(state.get("entries") or [])
    cycles_done = int(state.get("cycles_done", 0))
    expected_cycles = int(state.get("test_times", 0))
    reboot_failures = int(state.get("reboot_failures", 0))
    final_status = _resolve_final_status(state)
    avg_boot_s, max_boot_s = _compute_boot_stats(entries)

    ts = time.strftime("%Y%m%d_%H%M%S", time.localtime(now_fn()))
    run_id = f"powercycle_adb_{ts}_{serial}"

    metrics: dict[str, Any] = {
        "run_id": run_id,
        "driver": "host_adb",
        "cycles_done": cycles_done,
        "expected_cycles": expected_cycles,
        "reboot_failures": reboot_failures,
        "final_status": final_status,
        "avg_boot_duration_s": avg_boot_s,
        "max_boot_duration_s": max_boot_s,
        "device_online_on_finish": device_online_on_finish,
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
