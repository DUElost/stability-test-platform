# -*- coding: utf-8 -*-
"""FacAging (com.ape.aging) Aging DDR 专项巡检器（patrol 阶段）。

兼顾 SPRD DST 离线态与 Android 在线态的状态机巡检：
1. **设备离线（无 ADB）**：
   - DST 阶段展锐芯片处于无 USB 枚举的底层测试态，ADB 离线属正常现象；
   - 按 ``STP_JOB_ID`` 隔离本地状态文件，记录首次连续离线时间戳 ``offline_since``；
   - 连续离线时长 < ``max_offline_seconds``（默认 2400s）→ 返回 ``success=true``；
   - 超过阈值 → 返回 ``success=false`` 告警（可选 ``terminate_on_anomaly=true`` 提前终止）。
2. **设备在线（ADB 识别）**：
   - 清零离线计时；若 ``sys.boot_completed != "1"`` 则视为开机中，返回 ``success=true``；
   - 读取 ``aging_pipeline_session`` / ``ddr_test_session`` / ``ddr_test_report`` DataStore：
     - 终态 ``PASS``（``status==COMPLETED`` 或 ``DDR:DONE_PASS`` 或 ``report.result==PASS`` 且未运行）
       → 返回 ``{"success": true, "patrol_complete": true, ...}``，引擎立即结束巡检并进入 teardown；
     - 终态 ``FAIL``（``status==FAILED`` 或 ``DDR:DONE_FAIL`` 或 ``report.result==FAIL``）
       → 返回 ``{"success": false, "patrol_fail": true, ...}``，引擎立即结束巡检并进入 teardown 收取失败证据；
     - 运行中（``ddr.running==true`` 或 ``pipe.active==true`` 或 ``DdrTestService`` 存活）
       → 跟踪进度签名，在线连续停滞超过 ``online_stall_cycles`` 告警，否则返回 ``success=true``；
     - 非运行且无终态 → 豁免 ``offline -> online`` 首轮（开机续跑恢复窗），连续达到
       ``dead_grace_cycles`` 后报错。

STP_STEP_PARAMS:
{
    "max_offline_seconds": 2400,     // 连续离线预算秒数（覆盖单轮 DST + 重启）
    "dead_grace_cycles": 2,          // 在线但服务未运行宽限轮数
    "online_stall_cycles": 20,       // 在线且进度完全停滞的告警轮数（0=关闭）
    "terminate_on_anomaly": false    // 离线超时/停滞/异常停止是否同时发 patrol_fail 终结 Job
}
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from _lib import (
    adb_shell,
    ddr_service_alive,
    device_online,
    device_serial,
    ensure_adb_root,
    env,
    output_result,
    param_or_env,
    params,
    progress_stamp,
    read_datastore_file,
)

# P525AA 实机测量单轮 REBOOT_DST 离线耗时约 2h47m–2h51m（~10,300s），默认预算 4h（14400s）。
_DEFAULT_MAX_OFFLINE_SECONDS = 14400
_DEFAULT_DEAD_GRACE_CYCLES = 2
_DEFAULT_ONLINE_STALL_CYCLES = 20


def _state_file(serial: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", serial)
    return Path(tempfile.gettempdir()) / f"aging_ddr_check_{safe}.json"


def _load_state(serial: str, job_id: str) -> dict[str, Any]:
    path = _state_file(serial)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("job_id") == job_id:
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {
        "job_id": job_id,
        "last_state": None,
        "offline_since": None,
        "last_online_at": None,
        "dead_streak": 0,
        "stall_streak": 0,
        "last_progress_sig": None,
    }


def _save_state(serial: str, state: dict[str, Any]) -> None:
    path = _state_file(serial)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        sys.stderr.write(f"aging_ddr_check: save state failed: {exc}\n")


def _parse_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _build_progress_snapshot(
    pipe: dict[str, Any],
    ddr: dict[str, Any],
    rep: dict[str, Any],
    *,
    service_alive: bool,
    state_label: str,
) -> dict[str, Any]:
    return {
        "online": True,
        "state": state_label,
        "service_alive": service_alive,
        "session_id": pipe.get("session_id") or ddr.get("session_id") or "",
        "pipeline_active": pipe.get("active"),
        "pipeline_status": pipe.get("status", ""),
        "stage_status": pipe.get("stage_status", ""),
        "ddr_running": ddr.get("running"),
        "current_module_pass": ddr.get("current_module_pass"),
        "module_pass_count": ddr.get("module_pass_count"),
        "current_item_index": ddr.get("current_item_index"),
        "current_reboot_count": ddr.get("current_reboot_count"),
        "status_message": ddr.get("status_message", ""),
        "report_result": rep.get("result", ""),
        "last_error": pipe.get("last_error", ""),
    }


def _evaluate(cfg: dict[str, Any], *, now_fn=time.time) -> tuple[bool, dict[str, Any]]:
    serial = device_serial()
    job_id = env("STP_JOB_ID", "")
    max_offline = max(
        60,
        int(
            str(
                param_or_env(
                    cfg,
                    "max_offline_seconds",
                    "STP_AGING_DDR_MAX_OFFLINE_SECONDS",
                    _DEFAULT_MAX_OFFLINE_SECONDS,
                )
            ).strip()
        ),
    )
    dead_grace = max(
        1,
        int(
            str(
                param_or_env(
                    cfg,
                    "dead_grace_cycles",
                    "STP_AGING_DDR_DEAD_GRACE_CYCLES",
                    _DEFAULT_DEAD_GRACE_CYCLES,
                )
            ).strip()
        ),
    )
    online_stall = max(
        0,
        int(
            str(
                param_or_env(
                    cfg,
                    "online_stall_cycles",
                    "STP_AGING_DDR_ONLINE_STALL_CYCLES",
                    _DEFAULT_ONLINE_STALL_CYCLES,
                )
            ).strip()
        ),
    )
    terminate_on_anomaly = _parse_bool(
        param_or_env(cfg, "terminate_on_anomaly", "STP_AGING_DDR_TERMINATE_ON_ANOMALY", False)
    )

    state = _load_state(serial, job_id)
    now = float(now_fn())
    progress_stamp({"phase": "probe_device", "serial": serial, "job_id": job_id})

    # 1. 离线判定（DST / 重启窗）
    if not device_online(timeout=10):
        offline_since = state.get("offline_since")
        if not isinstance(offline_since, (int, float)) or offline_since <= 0:
            offline_since = now
            state["offline_since"] = offline_since
        state["last_state"] = "offline"
        _save_state(serial, state)

        elapsed = max(0.0, now - float(offline_since))
        progress = {
            "online": False,
            "state": "offline_in_dst_or_reboot",
            "offline_elapsed_seconds": int(elapsed),
            "max_offline_seconds": max_offline,
        }
        progress_stamp({"phase": "offline_budget", **progress})
        if elapsed >= max_offline:
            payload: dict[str, Any] = {
                "error_message": (
                    f"设备连续离线 {int(elapsed)}s（超过阈值 {max_offline}s），"
                    "可能关机/卡死在 DST 或重启未恢复"
                ),
                "progress": progress,
            }
            if terminate_on_anomaly:
                payload["patrol_fail"] = True
            return False, payload
        return True, {"progress": progress}

    # 2. 在线判定：先检查是否已开机完成
    came_from_offline = state.get("last_state") == "offline"
    state["offline_since"] = None
    state["last_state"] = "online"
    state["last_online_at"] = now

    boot_completed = adb_shell("getprop sys.boot_completed", timeout=10).strip()
    if boot_completed != "1":
        _save_state(serial, state)
        return True, {
            "progress": {
                "online": True,
                "boot_completed": boot_completed,
                "state": "booting",
            }
        }

    # 2.5 重启后 adbd 会掉回 uid=2000，读取 DataStore 前自动重提 adb root
    if not ensure_adb_root():
        if not device_online(timeout=5):
            state["last_state"] = "offline"
            state["offline_since"] = now
            _save_state(serial, state)
            return True, {
                "progress": {
                    "online": False,
                    "state": "adbd_restarting_for_root",
                }
            }
        _save_state(serial, state)
        return False, {
            "error_message": "设备在线但 adb root 恢复失败（uid != 0），无法读取 FacAging DataStore",
            "progress": {"online": True, "state": "root_denied"},
        }

    # 3. 读取三份核心 DataStore 与服务状态
    pipe_kind, pipe = read_datastore_file("aging_pipeline_session.preferences_pb", timeout=10)
    ddr_kind, ddr = read_datastore_file("ddr_test_session.preferences_pb", timeout=10)
    rep_kind, rep = read_datastore_file("ddr_test_report.preferences_pb", timeout=10)
    svc_alive, svc_rc = ddr_service_alive(timeout=10)

    # 探查中途若刚好触发下一轮 REBOOT_DST / REBOOT，全部读瞬态失败 → 按正常切换窗口放行
    if pipe_kind == "transient" and ddr_kind == "transient" and rep_kind == "transient" and svc_rc != 0:
        state["last_state"] = "offline"
        state["offline_since"] = now
        _save_state(serial, state)
        return True, {
            "progress": {
                "online": False,
                "state": "transient_reboot_mid_check",
            }
        }

    pipe_status = str(pipe.get("status") or "").strip().upper()
    stage_status = str(pipe.get("stage_status") or "").strip()
    report_result = str(rep.get("result") or "").strip().upper()
    ddr_running = ddr.get("running") is True
    pipe_active = pipe.get("active") is True

    # 4. 终态判定优先：FAIL -> 立即结束巡检并判 FAIL
    is_fail = (
        pipe_status == "FAILED"
        or "DDR:DONE_FAIL" in stage_status
        or report_result == "FAIL"
    )
    if is_fail:
        state["dead_streak"] = 0
        state["stall_streak"] = 0
        _save_state(serial, state)
        progress = _build_progress_snapshot(
            pipe, ddr, rep, service_alive=svc_alive, state_label="done_fail"
        )
        progress["run_finished"] = True
        progress["final_status"] = "FAIL"
        last_err = str(pipe.get("last_error") or ddr.get("status_message") or "").strip()
        err_detail = f" ({last_err})" if last_err else ""
        return False, {
            "patrol_fail": True,
            "error_message": (
                f"Aging DDR 测试失败: pipeline_status={pipe_status or '?'} "
                f"stage_status={stage_status or '?'} report_result={report_result or '?'}{err_detail}"
            ),
            "progress": progress,
        }

    # 5. 终态判定：PASS -> 立即结束巡检并进入 teardown
    is_pass = (
        pipe_status == "COMPLETED"
        or "DDR:DONE_PASS" in stage_status
        or (not ddr_running and not pipe_active and report_result == "PASS")
    )
    if is_pass:
        state["dead_streak"] = 0
        state["stall_streak"] = 0
        _save_state(serial, state)
        progress = _build_progress_snapshot(
            pipe, ddr, rep, service_alive=svc_alive, state_label="done_pass"
        )
        progress["run_finished"] = True
        progress["final_status"] = "PASS"
        progress_stamp({"phase": "done_pass", "session_id": progress["session_id"]})
        return True, {"patrol_complete": True, "progress": progress}

    # 6. 正常运行态判定 + 在线停滞检测
    if ddr_running or pipe_active or svc_alive:
        state["dead_streak"] = 0
        sig = [
            pipe.get("session_id"),
            stage_status,
            ddr.get("current_module_pass"),
            ddr.get("current_item_index"),
            ddr.get("current_reboot_count"),
            ddr.get("item_elapsed_sec"),
            len(str(rep.get("records") or "")),
        ]
        if state.get("last_progress_sig") == sig:
            state["stall_streak"] = int(state.get("stall_streak") or 0) + 1
        else:
            state["last_progress_sig"] = sig
            state["stall_streak"] = 0
        _save_state(serial, state)

        progress = _build_progress_snapshot(
            pipe, ddr, rep, service_alive=svc_alive, state_label="running"
        )
        progress["stall_streak"] = state["stall_streak"]
        progress_stamp(
            {
                "phase": "running",
                "module_pass": ddr.get("current_module_pass"),
                "module_pass_count": ddr.get("module_pass_count"),
                "item_index": ddr.get("current_item_index"),
                "reboot_count": ddr.get("current_reboot_count"),
            }
        )
        if online_stall > 0 and state["stall_streak"] >= online_stall:
            payload = {
                "error_message": (
                    f"Aging DDR 在线状态下连续 {state['stall_streak']} 轮无进度推进 "
                    f"(stage_status={stage_status or '?'}, pass={ddr.get('current_module_pass')}/"
                    f"{ddr.get('module_pass_count')}, item={ddr.get('current_item_index')})"
                ),
                "progress": progress,
            }
            if terminate_on_anomaly:
                payload["patrol_fail"] = True
            return False, payload
        return True, {"progress": progress}

    # 7. 在线但既未运行又无终态结果（豁免 offline -> online 首轮开机续跑恢复窗）
    if came_from_offline:
        _save_state(serial, state)
        progress = _build_progress_snapshot(
            pipe, ddr, rep, service_alive=svc_alive, state_label="boot_resume_grace"
        )
        return True, {"progress": progress}

    dead_streak = int(state.get("dead_streak") or 0) + 1
    state["dead_streak"] = dead_streak
    _save_state(serial, state)
    progress = _build_progress_snapshot(
        pipe, ddr, rep, service_alive=svc_alive, state_label="not_running"
    )
    progress["dead_streak"] = dead_streak
    if dead_streak >= dead_grace:
        payload = {
            "error_message": (
                f"Aging DDR 服务未运行且无 PASS/FAIL 终态（连续 {dead_streak} 轮，"
                f"pipe_kind={pipe_kind}, ddr_kind={ddr_kind}, stage_status={stage_status or '?'}）"
            ),
            "progress": progress,
        }
        if terminate_on_anomaly:
            payload["patrol_fail"] = True
        return False, payload
    return True, {"progress": progress}


def main() -> None:
    cfg = params()
    try:
        ok, payload = _evaluate(cfg)
    except Exception as exc:  # noqa: BLE001 — 脚本顶层统一输出结构化错误
        output_result(False, error_message=str(exc))
        sys.exit(1)
    output_result(ok, **payload)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
