# -*- coding: utf-8 -*-
"""FacAging (com.ape.aging) Aging DDR 足迹解除 / 探针（teardown 阶段）。

职责边界：只做停止服务、清除开机续跑足迹与回读校验，不拉取测试结果文件（结果收取归
``aging_ddr_finish``）。对于已完成（``COMPLETED`` / ``FAILED``）的会话，保留其终态字段
不被覆盖，仅确保 ``active=false``、``running=false`` 且 ``DdrTestService`` 已退出。

STP_STEP_PARAMS:
{
    "mode": "disarm",                     // disarm | probe
    "wait_device_online_seconds": 1800    // disarm 等待设备从 DST/重启恢复上线的秒数上限
}

stdout JSON:
{
    "success": true/false,
    "disarm_result": "confirmed" | "failed",
    "readback": "clear" | "footprint" | "readback_failed",
    "readback_summary": "...",
    "evidence": {...},
    "mode": "disarm" | "probe"
}
"""
from __future__ import annotations

import sys
import time
from typing import Any

from _lib import (
    DDR_SERVICE,
    DDR_SESSION_INT32_KEYS,
    DDR_STOP_ACTION,
    PACKAGE,
    adb,
    adb_shell,
    ddr_service_state,
    device_online,
    device_serial,
    ensure_adb_root,
    is_root,
    output_result,
    param_or_env,
    params,
    progress_stamp,
    read_datastore_file,
    write_datastore_file,
)

_DEFAULT_MODE = "disarm"
_DEFAULT_WAIT_ONLINE = 1800
_POLL_PERIOD_S = 10
_STATE_TIMEOUT_S = 2
_BOOT_TIMEOUT_S = 3


def _wait_device_online(
    timeout_s: int,
    *,
    clock=time.monotonic,
    sleeper=time.sleep,
    online_fn=None,
    boot_prop_fn=None,
    progress_fn=None,
) -> bool:
    """轮询 get-state==device 且 sys.boot_completed==1，最长 timeout_s 秒。"""
    timeout_s = max(0, int(timeout_s or 0))
    deadline = clock() + timeout_s
    online_fn = online_fn or (lambda t: device_online(timeout=t))
    boot_prop_fn = boot_prop_fn or (
        lambda t: adb_shell("getprop sys.boot_completed", timeout=t).strip()
    )
    progress_fn = progress_fn or progress_stamp
    seq = 0
    for _ in range(timeout_s // _POLL_PERIOD_S + 2):
        poll_start = clock()
        state_timeout = min(_STATE_TIMEOUT_S, int(deadline - poll_start))
        if state_timeout < 1:
            remaining = deadline - clock()
            if remaining > 0:
                sleeper(remaining)
            return False
        online, booted, err_text = False, "", ""
        try:
            online = bool(online_fn(state_timeout))
            if online:
                boot_timeout = min(_BOOT_TIMEOUT_S, int(deadline - clock()))
                if boot_timeout >= 1:
                    booted = str(boot_prop_fn(boot_timeout) or "").strip()
        except Exception as exc:  # noqa: BLE001 — 诊断入戳，不中断等待截止
            err_text = str(exc)[:200]
            online, booted = False, ""
        seq += 1
        payload: dict[str, Any] = {
            "phase": "wait_device_online",
            "seq": seq,
            "online": online,
            "boot_completed": booted,
            "remaining_s": max(0, int(deadline - clock())),
        }
        if err_text:
            payload["error"] = err_text
        progress_fn(payload)
        if online and booted == "1":
            return True
        delay = min(poll_start + _POLL_PERIOD_S, deadline) - clock()
        if delay > 0:
            sleeper(delay)
    return False


def _read_footprint_evidence() -> dict[str, Any]:
    pipe_kind, pipe = read_datastore_file("aging_pipeline_session.preferences_pb", timeout=10)
    ddr_kind, ddr = read_datastore_file("ddr_test_session.preferences_pb", timeout=10)
    svc_state, svc_rc = ddr_service_state(timeout=10)

    evidence: dict[str, Any] = {
        "root": bool(is_root()),
        "pipe_kind": pipe_kind,
        "pipe_active": pipe.get("active") if pipe_kind == "ok" else None,
        "pipe_status": pipe.get("status") if pipe_kind == "ok" else None,
        "ddr_kind": ddr_kind,
        "ddr_running": ddr.get("running") if ddr_kind == "ok" else None,
        "service_state": svc_state,
        "service_rc": svc_rc,
    }

    unreadable = {"denied", "transient", "corrupt"}
    if pipe_kind in unreadable or ddr_kind in unreadable or svc_state == "unknown":
        return {"readback": "readback_failed", "evidence": evidence, "pipe": pipe, "ddr": ddr}

    has_footprint = (
        (pipe_kind == "ok" and pipe.get("active") is True)
        or (ddr_kind == "ok" and ddr.get("running") is True)
        or svc_state == "alive"
    )
    return {
        "readback": "footprint" if has_footprint else "clear",
        "evidence": evidence,
        "pipe": pipe,
        "ddr": ddr,
    }


def _stop_and_clear() -> dict[str, Any]:
    stop_rc, _, _ = adb("shell", f"am start-service -n {DDR_SERVICE} -a {DDR_STOP_ACTION}", timeout=15)
    time.sleep(1.0)
    force_rc, _, _ = adb("shell", f"am force-stop {PACKAGE}", timeout=15)
    time.sleep(0.5)

    pipe_kind, pipe = read_datastore_file("aging_pipeline_session.preferences_pb", timeout=10)
    ddr_kind, ddr = read_datastore_file("ddr_test_session.preferences_pb", timeout=10)

    pipe_rewritten = False
    if pipe_kind == "ok" and (
        pipe.get("active") is True or str(pipe.get("status") or "").upper() == "RUNNING"
    ):
        updated_pipe = dict(pipe)
        updated_pipe["active"] = False
        if str(updated_pipe.get("status") or "").upper() == "RUNNING":
            updated_pipe["status"] = "STOPPED"
        write_datastore_file("aging_pipeline_session.preferences_pb", updated_pipe)
        pipe_rewritten = True

    ddr_rewritten = False
    if ddr_kind == "ok" and ddr.get("running") is True:
        updated_ddr = dict(ddr)
        updated_ddr["running"] = False
        write_datastore_file(
            "ddr_test_session.preferences_pb",
            updated_ddr,
            int32_keys=DDR_SESSION_INT32_KEYS,
        )
        ddr_rewritten = True

    # 再次 force-stop 确保内存态不再回写旧 DataStore
    adb("shell", f"am force-stop {PACKAGE}", timeout=15)
    return {
        "stop_service_rc": stop_rc,
        "force_stop_rc": force_rc,
        "pipe_rewritten": pipe_rewritten,
        "ddr_rewritten": ddr_rewritten,
    }


def _run(cfg: dict[str, Any]) -> dict[str, Any]:
    mode = str(param_or_env(cfg, "mode", "STP_AGING_DDR_DISARM_MODE", _DEFAULT_MODE)).strip().lower()
    if mode not in ("disarm", "probe"):
        raise RuntimeError(f"mode 必须是 probe 或 disarm，收到: {mode!r}")

    wait_online = int(
        str(
            param_or_env(
                cfg,
                "wait_device_online_seconds",
                "STP_AGING_DDR_WAIT_ONLINE_SECONDS",
                _DEFAULT_WAIT_ONLINE,
            )
        )
        or _DEFAULT_WAIT_ONLINE
    )
    serial = device_serial()
    progress_stamp({"phase": "aging_ddr_disarm", "mode": mode, "serial": serial})

    if mode == "disarm":
        if not _wait_device_online(wait_online):
            return {
                "success": False,
                "disarm_result": "failed",
                "readback": "readback_failed",
                "readback_summary": f"设备 {serial} 在 {wait_online}s 内未上线，无法解除 Aging DDR 足迹",
                "evidence": {"online": False, "wait_device_online_seconds": wait_online},
                "mode": mode,
            }
        if not ensure_adb_root():
            return {
                "success": False,
                "disarm_result": "failed",
                "readback": "readback_failed",
                "readback_summary": f"设备 {serial} 重启后 adb root 恢复失败（uid != 0），无法读写 DataStore",
                "evidence": {"online": True, "root": False},
                "mode": mode,
            }
        action_steps = _stop_and_clear()
        progress_stamp({"phase": "readback_verify", "serial": serial})
        snap = _read_footprint_evidence()
        readback = snap["readback"]
        evidence = {**snap["evidence"], "stop_steps": action_steps}
        ok = readback == "clear"
        summary = (
            "Aging DDR 已停止，DataStore 续跑标志与 DdrTestService 均已确认清除"
            if ok
            else f"Aging DDR 解除后回读未清（readback={readback}）"
        )
        return {
            "success": ok,
            "disarm_result": "confirmed" if ok else "failed",
            "readback": readback,
            "readback_summary": summary,
            "evidence": evidence,
            "mode": mode,
        }

    # probe 模式：只回读，不改设备
    if not device_online(timeout=5):
        return {
            "success": False,
            "disarm_result": "failed",
            "readback": "readback_failed",
            "readback_summary": f"设备 {serial} 离线，无法探查 Aging DDR 足迹",
            "evidence": {"online": False},
            "mode": mode,
        }
    ensure_adb_root()
    snap = _read_footprint_evidence()
    readback = snap["readback"]
    ok = readback == "clear"
    return {
        "success": ok,
        "disarm_result": "confirmed" if ok else "failed",
        "readback": readback,
        "readback_summary": f"probe readback={readback}",
        "evidence": snap["evidence"],
        "mode": mode,
    }


def main() -> None:
    cfg = params()
    try:
        res = _run(cfg)
    except Exception as exc:  # noqa: BLE001 — 脚本顶层统一输出结构化错误
        output_result(
            False,
            disarm_result="failed",
            readback="readback_failed",
            error_message=str(exc),
        )
        sys.exit(1)
    ok = bool(res.pop("success", False))
    if not ok and "error_message" not in res:
        res["error_message"] = res.get("readback_summary") or "aging_ddr_disarm failed"
    output_result(ok, **res)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
