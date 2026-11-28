# -*- coding: utf-8 -*-
"""宿主侧 ADB 驱动休眠唤醒专项——巡检与循环推进器（patrol 阶段）。

以短时步进状态机驱动「亮屏保持 → 灭屏休眠 → 唤醒解锁」循环，不长占 Agent permit：
- ``WAIT_WAKE``：设备处于亮屏状态，保持满 ``wake_seconds``（剩余 <= ``inline_wait_max_seconds``
  时内联补齐，保证精确控时）后下发 ``KEYCODE_SLEEP``，核验 ``mWakefulness`` 进入
  ``Asleep``/``Dozing``；若未进入则按 ``sleep_retry_attempts`` 重试并记录
  ``sleep_anomalies``，随后切入 ``WAIT_SLEEP``；
- ``WAIT_SLEEP``：设备处于灭屏休眠态。若未满 ``sleep_seconds``（且剩余 >
  ``inline_wait_max_seconds``）直接返回释放 permit（顺便轻量观测电源态；若意外自唤醒则
  记异常并可重新息屏）；若已满 ``sleep_seconds``（或剩余 <= ``inline_wait_max_seconds``
  内联补齐），下发 ``KEYCODE_WAKEUP`` + 解锁回桌面并核验 ``mWakefulness == Awake``：
  - 若唤醒成功：记账本轮 ``PASS``（``cycles_done += 1``）；达到 ``test_times`` 时返回
    ``patrol_complete=true``，否则切回 ``WAIT_WAKE`` 开启下一轮；
  - 若连续重试仍无法唤醒或设备在 ``wake_timeout_seconds`` 内失联：记 ``wake_failures``，
    写失败明细并输出 ``patrol_fail=true`` 终止 Job。
"""
from __future__ import annotations

import sys
import time
from typing import Any, Callable

from _lib import (
    _DEFAULT_INLINE_WAIT_MAX_SECONDS,
    _DEFAULT_PROJECT,
    _DEFAULT_SLEEP_CONFIRM_SECONDS,
    _DEFAULT_SLEEP_RETRY_ATTEMPTS,
    _DEFAULT_SLEEP_SECONDS,
    _DEFAULT_TEST_TIMES,
    _DEFAULT_WAKE_RETRY_ATTEMPTS,
    _DEFAULT_WAKE_SECONDS,
    _DEFAULT_WAKE_TIMEOUT_SECONDS,
    device_online,
    device_serial,
    format_ts,
    is_asleep_state,
    is_awake_state,
    job_id,
    load_state,
    output_result,
    param_or_env,
    params,
    parse_bool,
    parse_int,
    progress_stamp,
    read_wakefulness,
    save_state,
    trigger_sleep,
    trigger_wakeup,
)


def _resolve_override(
    cfg: dict[str, Any],
    state: dict[str, Any],
    key: str,
    env_key: str,
    default: Any,
) -> Any:
    raw = cfg.get(key)
    if raw is not None and str(raw).strip() != "":
        return raw
    if key in state and state[key] is not None and str(state[key]).strip() != "":
        return state[key]
    return param_or_env(cfg, key, env_key, default)


def _effective_settings(cfg: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    return {
        "test_times": parse_int(
            _resolve_override(cfg, state, "test_times", "STP_SLEEP_TEST_TIMES", _DEFAULT_TEST_TIMES),
            name="test_times",
            minimum=0,
        ),
        "wake_seconds": parse_int(
            _resolve_override(cfg, state, "wake_seconds", "STP_SLEEP_WAKE_SECONDS", _DEFAULT_WAKE_SECONDS),
            name="wake_seconds",
            minimum=5,
        ),
        "sleep_seconds": parse_int(
            _resolve_override(cfg, state, "sleep_seconds", "STP_SLEEP_SLEEP_SECONDS", _DEFAULT_SLEEP_SECONDS),
            name="sleep_seconds",
            minimum=5,
        ),
        "wake_timeout_seconds": parse_int(
            _resolve_override(
                cfg, state, "wake_timeout_seconds", "STP_SLEEP_WAKE_TIMEOUT_SECONDS", _DEFAULT_WAKE_TIMEOUT_SECONDS
            ),
            name="wake_timeout_seconds",
            minimum=10,
        ),
        "sleep_confirm_seconds": parse_int(
            _resolve_override(
                cfg, state, "sleep_confirm_seconds", "STP_SLEEP_SLEEP_CONFIRM_SECONDS", _DEFAULT_SLEEP_CONFIRM_SECONDS
            ),
            name="sleep_confirm_seconds",
            minimum=2,
        ),
        "wake_retry_attempts": parse_int(
            _resolve_override(
                cfg, state, "wake_retry_attempts", "STP_SLEEP_WAKE_RETRY_ATTEMPTS", _DEFAULT_WAKE_RETRY_ATTEMPTS
            ),
            name="wake_retry_attempts",
            minimum=0,
        ),
        "sleep_retry_attempts": parse_int(
            _resolve_override(
                cfg, state, "sleep_retry_attempts", "STP_SLEEP_SLEEP_RETRY_ATTEMPTS", _DEFAULT_SLEEP_RETRY_ATTEMPTS
            ),
            name="sleep_retry_attempts",
            minimum=0,
        ),
        "inline_wait_max_seconds": parse_int(
            param_or_env(
                cfg,
                "inline_wait_max_seconds",
                "STP_SLEEP_INLINE_WAIT_MAX_SECONDS",
                _DEFAULT_INLINE_WAIT_MAX_SECONDS,
            ),
            name="inline_wait_max_seconds",
            minimum=0,
        ),
        "unlock_to_home": parse_bool(
            _resolve_override(cfg, state, "unlock_to_home", "STP_SLEEP_UNLOCK_TO_HOME", True),
            default=True,
        ),
    }


def _bootstrap_state(serial: str, cur_job_id: str, cfg: dict[str, Any], now: float) -> dict[str, Any]:
    project = str(param_or_env(cfg, "project", "STP_SLEEP_PROJECT", _DEFAULT_PROJECT)).strip() or _DEFAULT_PROJECT
    return {
        "schema_version": 1,
        "driver": "host_adb",
        "job_id": cur_job_id,
        "serial": serial,
        "project": project,
        "phase": "WAIT_WAKE",
        "seq": 0,
        "current_cycle": 1,
        "cycles_done": 0,
        "wake_failures": 0,
        "sleep_anomalies": 0,
        "wake_started_at": now,
        "sleep_started_at": None,
        "started_at": now,
        "finished_at": None,
        "final_status": "",
        "failure_reason": "",
        "entries": [],
    }


def _progress_payload(
    state: dict[str, Any],
    settings: dict[str, Any],
    *,
    observed: str,
    wakefulness: str = "",
) -> dict[str, Any]:
    seq = int(state.get("seq", 0)) + 1
    state["seq"] = seq
    return {
        "seq": seq,
        "step": "sleep_adb_patrol",
        "driver": "host_adb",
        "phase": state.get("phase", ""),
        "current_cycle": int(state.get("current_cycle", 1)),
        "cycles_done": int(state.get("cycles_done", 0)),
        "expected_cycles": settings["test_times"],
        "wake_failures": int(state.get("wake_failures", 0)),
        "sleep_anomalies": int(state.get("sleep_anomalies", 0)),
        "wakefulness": wakefulness,
        "observed": observed,
    }


def _enter_sleep_and_verify(
    settings: dict[str, Any],
    *,
    now_fn: Callable[[], float],
    sleep_fn: Callable[[float], None],
) -> tuple[bool, str]:
    """下发 KEYCODE_SLEEP 并确认 mWakefulness 进入休眠态（带重试）。"""
    attempts = 1 + settings["sleep_retry_attempts"]
    last_state = ""
    for _ in range(attempts):
        trigger_sleep()
        deadline = now_fn() + settings["sleep_confirm_seconds"]
        polls = 0
        while polls < 20:
            polls += 1
            sleep_fn(min(1.5, max(0.2, deadline - now_fn())))
            last_state = read_wakefulness()
            if is_asleep_state(last_state):
                return True, last_state
            if now_fn() >= deadline:
                break
    return False, last_state


def _wake_and_verify(
    settings: dict[str, Any],
    *,
    now_fn: Callable[[], float],
    sleep_fn: Callable[[float], None],
) -> tuple[bool, str]:
    """下发 KEYCODE_WAKEUP + 解锁并确认 mWakefulness 回到 Awake（带重试）。"""
    attempts = 1 + settings["wake_retry_attempts"]
    last_state = ""
    for _ in range(attempts):
        trigger_wakeup(unlock=settings["unlock_to_home"])
        deadline = now_fn() + min(15.0, float(settings["wake_timeout_seconds"]))
        polls = 0
        while polls < 20:
            polls += 1
            sleep_fn(min(1.5, max(0.2, deadline - now_fn())))
            last_state = read_wakefulness()
            if is_awake_state(last_state):
                return True, last_state
            if now_fn() >= deadline:
                break
    return False, last_state


def _fail_terminal(
    state: dict[str, Any],
    settings: dict[str, Any],
    *,
    serial: str,
    reason_code: str,
    message: str,
    now: float,
    wakefulness: str = "",
) -> dict[str, Any]:
    cycle = int(state.get("current_cycle", 1))
    entries = list(state.get("entries") or [])
    entries.append(
        {
            "cycle": cycle,
            "status": "FAIL",
            "reason": reason_code,
            "message": message,
            "failed_at": format_ts(now),
            "wakefulness": wakefulness,
        }
    )
    state["entries"] = entries
    state["wake_failures"] = int(state.get("wake_failures", 0)) + 1
    state["phase"] = "FAILED"
    state["final_status"] = "FAIL"
    state["failure_reason"] = message
    state["finished_at"] = now
    payload = _progress_payload(state, settings, observed="failed", wakefulness=wakefulness)
    payload["failure_reason"] = message
    save_state(state, serial=serial)
    progress_stamp(payload)
    return {
        "success": False,
        "patrol_fail": True,
        "error_message": message,
        "progress": payload,
    }


def _run(
    cfg: dict[str, Any],
    *,
    now_fn: Callable[[], float] = time.time,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    serial = device_serial()
    cur_job_id = job_id()
    now = now_fn()

    state = load_state(expected_job_id=cur_job_id, serial=serial)
    if state is None:
        state = _bootstrap_state(serial, cur_job_id, cfg, now)

    settings = _effective_settings(cfg, state)
    state["test_times"] = settings["test_times"]
    state["wake_seconds"] = settings["wake_seconds"]
    state["sleep_seconds"] = settings["sleep_seconds"]
    state["wake_timeout_seconds"] = settings["wake_timeout_seconds"]
    state["sleep_confirm_seconds"] = settings["sleep_confirm_seconds"]
    state["unlock_to_home"] = settings["unlock_to_home"]

    phase = str(state.get("phase") or "WAIT_WAKE")

    if phase == "COMPLETED":
        payload = _progress_payload(state, settings, observed="completed", wakefulness="Awake")
        payload["run_finished"] = True
        save_state(state, serial=serial)
        progress_stamp(payload)
        return {"success": True, "patrol_complete": True, "progress": payload}

    if phase == "FAILED":
        msg = str(state.get("failure_reason") or "宿主侧休眠唤醒压测已进入失败终态")
        payload = _progress_payload(state, settings, observed="failed")
        save_state(state, serial=serial)
        progress_stamp(payload)
        return {"success": False, "patrol_fail": True, "error_message": msg, "progress": payload}

    if phase == "WAIT_WAKE":
        wake_started_at = float(state.get("wake_started_at") or now)
        remaining = float(settings["wake_seconds"]) - max(0.0, now - wake_started_at)
        if remaining > settings["inline_wait_max_seconds"]:
            wf = read_wakefulness()
            payload = _progress_payload(state, settings, observed="waiting_wake", wakefulness=wf)
            payload["wake_remaining_s"] = round(remaining, 1)
            save_state(state, serial=serial)
            progress_stamp(payload)
            return {"success": True, "progress": payload}

        if remaining > 0:
            sleep_fn(remaining)

        slept_ok, wf_after = _enter_sleep_and_verify(settings, now_fn=now_fn, sleep_fn=sleep_fn)
        if not slept_ok:
            # 记录一次休眠异常：设备可能临时持有 WakeLock；仅当既不在线又无状态时才失败
            if not device_online():
                return _fail_terminal(
                    state,
                    settings,
                    serial=serial,
                    reason_code="device_offline_before_sleep",
                    message=f"第 {state['current_cycle']} 轮尝试休眠时设备离线",
                    now=now_fn(),
                    wakefulness=wf_after,
                )
            state["sleep_anomalies"] = int(state.get("sleep_anomalies", 0)) + 1
            sleep_at = now_fn()
            state["sleep_started_at"] = sleep_at
            state["phase"] = "WAIT_SLEEP"
            payload = _progress_payload(state, settings, observed="sleep_anomaly_unconfirmed", wakefulness=wf_after)
            save_state(state, serial=serial)
            progress_stamp(payload)
            return {"success": True, "progress": payload}

        sleep_at = now_fn()
        state["sleep_started_at"] = sleep_at
        state["phase"] = "WAIT_SLEEP"
        payload = _progress_payload(state, settings, observed="entered_sleep", wakefulness=wf_after)
        save_state(state, serial=serial)
        progress_stamp(payload)
        return {"success": True, "progress": payload}

    if phase == "WAIT_SLEEP":
        sleep_started_at = float(state.get("sleep_started_at") or now)
        elapsed_sleep = max(0.0, now - sleep_started_at)
        remaining = float(settings["sleep_seconds"]) - elapsed_sleep

        if remaining > settings["inline_wait_max_seconds"]:
            wf = read_wakefulness()
            obs = "sleeping"
            if wf and is_awake_state(wf):
                state["sleep_anomalies"] = int(state.get("sleep_anomalies", 0)) + 1
                obs = "unexpected_awake_re_sleep"
                trigger_sleep()
            elif not wf and not device_online():
                # 某些平台深睡时 ADB 暂时不可达属正常，记录 sleeping_offline，待 sleep 到期后尝试唤醒
                obs = "sleeping_offline"
            payload = _progress_payload(state, settings, observed=obs, wakefulness=wf)
            payload["sleep_remaining_s"] = round(remaining, 1)
            save_state(state, serial=serial)
            progress_stamp(payload)
            return {"success": True, "progress": payload}

        if remaining > 0:
            sleep_fn(remaining)

        woke_ok, wf_after = _wake_and_verify(settings, now_fn=now_fn, sleep_fn=sleep_fn)
        now_done = now_fn()
        if not woke_ok:
            # 若设备在到期唤醒窗口内仍未回到 Awake（或持续失联），再按 wake_timeout_seconds 检查是否超时
            overdue = max(0.0, now_done - (sleep_started_at + float(settings["sleep_seconds"])))
            if overdue >= float(settings["wake_timeout_seconds"]) or device_online():
                return _fail_terminal(
                    state,
                    settings,
                    serial=serial,
                    reason_code="wake_failed",
                    message=(
                        f"第 {state['current_cycle']} 轮唤醒失败"
                        f"（mWakefulness={wf_after or 'offline'}，超过睡眠目标 {overdue:.1f}s）"
                    ),
                    now=now_done,
                    wakefulness=wf_after,
                )
            payload = _progress_payload(state, settings, observed="wake_retry_wait", wakefulness=wf_after)
            save_state(state, serial=serial)
            progress_stamp(payload)
            return {"success": True, "progress": payload}

        cycle = int(state.get("current_cycle", 1))
        actual_sleep_s = round(max(0.0, now_done - sleep_started_at), 2)
        entries = list(state.get("entries") or [])
        entries.append(
            {
                "cycle": cycle,
                "status": "PASS",
                "sleep_started_at": format_ts(sleep_started_at),
                "woke_at": format_ts(now_done),
                "actual_sleep_s": actual_sleep_s,
                "wakefulness_after": wf_after,
            }
        )
        state["entries"] = entries
        state["cycles_done"] = int(state.get("cycles_done", 0)) + 1

        target = settings["test_times"]
        if target > 0 and state["cycles_done"] >= target:
            state["phase"] = "COMPLETED"
            state["final_status"] = "PASS"
            state["finished_at"] = now_done
            payload = _progress_payload(state, settings, observed="completed", wakefulness=wf_after)
            payload["run_finished"] = True
            save_state(state, serial=serial)
            progress_stamp(payload)
            return {"success": True, "patrol_complete": True, "progress": payload}

        state["current_cycle"] = cycle + 1
        state["wake_started_at"] = now_done
        state["sleep_started_at"] = None
        state["phase"] = "WAIT_WAKE"
        payload = _progress_payload(state, settings, observed="cycle_pass_awake", wakefulness=wf_after)
        save_state(state, serial=serial)
        progress_stamp(payload)
        return {"success": True, "progress": payload}

    raise RuntimeError(f"未知状态机 phase: {phase!r}")


def main() -> None:
    cfg = params()
    try:
        result = _run(cfg)
    except Exception as exc:  # noqa: BLE001
        output_result(False, error_message=str(exc))
        sys.exit(1)
    success = bool(result.pop("success"))
    output_result(success, **result)
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
