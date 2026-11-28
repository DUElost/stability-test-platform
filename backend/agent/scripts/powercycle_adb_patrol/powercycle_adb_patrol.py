# -*- coding: utf-8 -*-
"""宿主侧 ADB 驱动开关机专项——巡检与循环推进器（patrol 阶段）。

v1.0.1（2026-11-28，PlanRun 1765 dev=901 第 607 轮取证）：
- 新增有界补发重试 ``reboot_retry_attempts``（默认 2，0=禁用）：当某轮下发
  ``adb reboot`` 遭遇瞬态 USB/adbd 抖动（如 ``error: device ... not found``）
  且在 ``reboot_confirm_timeout_seconds`` 后仍观测到设备在线、内核 ``boot_id``
  未变化时，先补发 ``adb reboot`` 并重置确认计时，耗尽重试仍未生效才判
  ``reboot_not_effective`` 终止，避免 7 天数千轮长跑被单次瞬态抖动误杀。

以短时步进状态机驱动整轮开关机压测，不长期占用 Agent 执行 permit：
- ``IDLE``：记录初始 boot_id → 下发第 1 轮 ``adb reboot`` → 切到 ``WAIT_REBOOT``；
- ``WAIT_REBOOT``：
  - 设备离线或 ``sys.boot_completed != 1``：未超 ``boot_timeout_seconds`` 正常返回，
    超时则写失败明细并输出 ``patrol_fail=true`` 提前终止 Job；
  - 设备在线且 ``sys.boot_completed == 1``：核对内核 ``boot_id``：
    - 未变化：在 ``reboot_confirm_timeout_seconds`` 内继续等待；超时后若未耗尽
      ``reboot_retry_attempts`` 则补发 ``adb reboot`` 并重置确认计时，耗尽仍未
      变化则判重启未生效并输出 ``patrol_fail=true``；
    - 已变化：确认真实重启 → 执行唤醒/解锁/回主界面 → 记账本轮 PASS：
      - 若 ``test_times > 0`` 且已达目标次数 → 输出 ``patrol_complete=true`` 结束巡检；
      - 否则切到 ``WAIT_INTERVAL``（若 ``wait_seconds <= inline_wait_max_seconds``，
        直接在当步内短等后触发下一轮 ``adb reboot``，保证 10s 等短间隔精度）。
- ``WAIT_INTERVAL``：等待开机就绪并到达主界面后满 ``wait_seconds`` 秒，再触发下一轮
  ``adb reboot``（剩余时间 ``<= inline_wait_max_seconds`` 时内联补齐，精确控时）。

STP_STEP_PARAMS（均可省略，默认继承 powercycle_adb_setup 落盘配置）:
{
    "test_times": 100,
    "wait_seconds": 30,
    "boot_timeout_seconds": 300,
    "reboot_confirm_timeout_seconds": 30,
    "reboot_retry_attempts": 2,
    "unlock_to_home": true,
    "inline_wait_max_seconds": 15
}
"""
from __future__ import annotations

import sys
import time
from typing import Any, Callable

from _lib import (
    _DEFAULT_BOOT_TIMEOUT_SECONDS,
    _DEFAULT_INLINE_WAIT_MAX_SECONDS,
    _DEFAULT_PROJECT,
    _DEFAULT_REBOOT_CONFIRM_TIMEOUT_SECONDS,
    _DEFAULT_TEST_TIMES,
    _DEFAULT_WAIT_SECONDS,
    device_serial,
    format_ts,
    job_id,
    load_state,
    output_result,
    param_or_env,
    params,
    parse_bool,
    parse_int,
    progress_stamp,
    read_boot_id,
    save_state,
    system_ready,
    trigger_adb_reboot,
    unlock_to_home,
)


_DEFAULT_REBOOT_RETRY_ATTEMPTS = 2


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
    test_times = parse_int(
        _resolve_override(cfg, state, "test_times", "STP_POWER_CYCLE_TEST_TIMES", _DEFAULT_TEST_TIMES),
        name="test_times",
        minimum=0,
    )
    wait_seconds = parse_int(
        _resolve_override(cfg, state, "wait_seconds", "STP_POWER_CYCLE_WAIT_SECONDS", _DEFAULT_WAIT_SECONDS),
        name="wait_seconds",
        minimum=1,
    )
    boot_timeout_seconds = parse_int(
        _resolve_override(
            cfg,
            state,
            "boot_timeout_seconds",
            "STP_POWER_CYCLE_BOOT_TIMEOUT_SECONDS",
            _DEFAULT_BOOT_TIMEOUT_SECONDS,
        ),
        name="boot_timeout_seconds",
        minimum=30,
    )
    reboot_confirm_timeout_seconds = parse_int(
        _resolve_override(
            cfg,
            state,
            "reboot_confirm_timeout_seconds",
            "STP_POWER_CYCLE_REBOOT_CONFIRM_TIMEOUT_SECONDS",
            _DEFAULT_REBOOT_CONFIRM_TIMEOUT_SECONDS,
        ),
        name="reboot_confirm_timeout_seconds",
        minimum=5,
    )
    reboot_retry_attempts = parse_int(
        _resolve_override(
            cfg,
            state,
            "reboot_retry_attempts",
            "STP_POWER_CYCLE_REBOOT_RETRY_ATTEMPTS",
            _DEFAULT_REBOOT_RETRY_ATTEMPTS,
        ),
        name="reboot_retry_attempts",
        minimum=0,
    )
    unlock = parse_bool(
        _resolve_override(cfg, state, "unlock_to_home", "STP_POWER_CYCLE_UNLOCK_TO_HOME", True),
        default=True,
    )
    inline_wait_max_seconds = parse_int(
        param_or_env(
            cfg,
            "inline_wait_max_seconds",
            "STP_POWER_CYCLE_INLINE_WAIT_MAX_SECONDS",
            _DEFAULT_INLINE_WAIT_MAX_SECONDS,
        ),
        name="inline_wait_max_seconds",
        minimum=0,
    )
    return {
        "test_times": test_times,
        "wait_seconds": wait_seconds,
        "boot_timeout_seconds": boot_timeout_seconds,
        "reboot_confirm_timeout_seconds": reboot_confirm_timeout_seconds,
        "reboot_retry_attempts": reboot_retry_attempts,
        "unlock_to_home": unlock,
        "inline_wait_max_seconds": inline_wait_max_seconds,
    }


def _bootstrap_state(serial: str, cur_job_id: str, cfg: dict[str, Any], now: float) -> dict[str, Any]:
    project = str(
        param_or_env(cfg, "project", "STP_POWER_CYCLE_PROJECT", _DEFAULT_PROJECT)
    ).strip() or _DEFAULT_PROJECT
    return {
        "schema_version": 1,
        "driver": "host_adb",
        "job_id": cur_job_id,
        "serial": serial,
        "project": project,
        "phase": "IDLE",
        "seq": 0,
        "current_cycle": 0,
        "cycles_done": 0,
        "reboot_failures": 0,
        "initial_boot_id": "",
        "last_boot_id": "",
        "reboot_sent_at": None,
        "ready_at": None,
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
    device_online: bool,
    observed: str,
) -> dict[str, Any]:
    seq = int(state.get("seq", 0)) + 1
    state["seq"] = seq
    return {
        "seq": seq,
        "step": "powercycle_adb_patrol",
        "driver": "host_adb",
        "phase": state.get("phase", ""),
        "current_cycle": int(state.get("current_cycle", 0)),
        "cycles_done": int(state.get("cycles_done", 0)),
        "expected_cycles": settings["test_times"],
        "reboot_failures": int(state.get("reboot_failures", 0)),
        "device_online": device_online,
        "observed": observed,
    }


def _dispatch_next_reboot(
    state: dict[str, Any],
    *,
    now_fn: Callable[[], float],
) -> dict[str, Any]:
    """下发下一轮 adb reboot 并推进状态至 WAIT_REBOOT。

    注意：部分设备收到 adb reboot 后会立刻断开 USB，导致 adb 返回非零 rc；
    因此这里不以 rc!=0 直接判死，而是统一进入 WAIT_REBOOT，交由后续内核
    ``boot_id`` 变化与 ``reboot_confirm_timeout_seconds`` 做事实核验。
    """
    next_cycle = int(state.get("cycles_done", 0)) + 1
    sent_at = now_fn()
    rc, _, err = trigger_adb_reboot()
    state["current_cycle"] = next_cycle
    state["reboot_sent_at"] = sent_at
    state["ready_at"] = None
    state["phase"] = "WAIT_REBOOT"
    state["reboot_retries_used"] = 0
    state["last_reboot_rc"] = rc
    state["last_reboot_err"] = err.strip()[:200] if err else ""
    return {"rc": rc, "err": state["last_reboot_err"]}


def _fail_terminal(
    state: dict[str, Any],
    settings: dict[str, Any],
    *,
    serial: str,
    reason_code: str,
    message: str,
    elapsed: float,
    now: float,
    device_online: bool,
    observed: str,
) -> dict[str, Any]:
    cycle = int(state.get("current_cycle", 0)) or (int(state.get("cycles_done", 0)) + 1)
    entries = list(state.get("entries") or [])
    entries.append(
        {
            "cycle": cycle,
            "status": "FAIL",
            "reason": reason_code,
            "message": message,
            "reboot_sent_at": format_ts(float(state["reboot_sent_at"]))
            if state.get("reboot_sent_at") is not None
            else "",
            "failed_at": format_ts(now),
            "elapsed_s": round(elapsed, 2),
        }
    )
    state["entries"] = entries
    state["reboot_failures"] = int(state.get("reboot_failures", 0)) + 1
    state["phase"] = "FAILED"
    state["final_status"] = "FAIL"
    state["failure_reason"] = message
    state["finished_at"] = now
    payload = _progress_payload(state, settings, device_online=device_online, observed=observed)
    payload["failure_reason"] = message
    save_state(state, serial=serial)
    progress_stamp(payload)
    return {
        "success": False,
        "patrol_fail": True,
        "error_message": message,
        "progress": payload,
    }


def _maybe_wait_and_reboot(
    state: dict[str, Any],
    settings: dict[str, Any],
    *,
    serial: str,
    remaining: float,
    now_fn: Callable[[], float],
    sleep_fn: Callable[[float], None],
) -> bool:
    """若剩余等待时间落在内联阈值内，短等后立刻触发下一轮重启，返回 True。"""
    if remaining > settings["inline_wait_max_seconds"]:
        return False
    # 先落盘当前 WAIT_INTERVAL 状态，防止内联 sleep 期间被 SIGTERM 丢失本轮记账
    save_state(state, serial=serial)
    if remaining > 0:
        sleep_fn(remaining)
    _dispatch_next_reboot(state, now_fn=now_fn)
    return True


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
    # 回写生效参数，供 teardown 统一消费
    state["test_times"] = settings["test_times"]
    state["wait_seconds"] = settings["wait_seconds"]
    state["boot_timeout_seconds"] = settings["boot_timeout_seconds"]
    state["reboot_confirm_timeout_seconds"] = settings["reboot_confirm_timeout_seconds"]
    state["reboot_retry_attempts"] = settings["reboot_retry_attempts"]
    state["unlock_to_home"] = settings["unlock_to_home"]

    phase = str(state.get("phase") or "IDLE")

    if phase == "COMPLETED":
        payload = _progress_payload(state, settings, device_online=True, observed="completed")
        payload["run_finished"] = True
        save_state(state, serial=serial)
        progress_stamp(payload)
        return {"success": True, "patrol_complete": True, "progress": payload}

    if phase == "FAILED":
        msg = str(state.get("failure_reason") or "宿主侧开关机压测已进入失败终态")
        payload = _progress_payload(state, settings, device_online=False, observed="failed")
        save_state(state, serial=serial)
        progress_stamp(payload)
        return {"success": False, "patrol_fail": True, "error_message": msg, "progress": payload}

    if phase == "IDLE":
        ready, observed = system_ready()
        if not ready:
            elapsed = max(0.0, now - float(state.get("started_at") or now))
            if elapsed >= settings["boot_timeout_seconds"]:
                return _fail_terminal(
                    state,
                    settings,
                    serial=serial,
                    reason_code="initial_not_ready",
                    message=(
                        f"首轮启动前设备持续未就绪（{observed}，已等待 "
                        f"{elapsed:.1f}s >= {settings['boot_timeout_seconds']}s）"
                    ),
                    elapsed=elapsed,
                    now=now,
                    device_online=False,
                    observed=observed,
                )
            payload = _progress_payload(state, settings, device_online=False, observed=observed)
            save_state(state, serial=serial)
            progress_stamp(payload)
            return {"success": True, "progress": payload}

        if not state.get("last_boot_id"):
            boot_id = read_boot_id()
            if not boot_id:
                return _fail_terminal(
                    state,
                    settings,
                    serial=serial,
                    reason_code="boot_id_unreadable",
                    message="无法读取设备内核 /proc/sys/kernel/random/boot_id，终止测试",
                    elapsed=0.0,
                    now=now,
                    device_online=True,
                    observed="boot_id_empty",
                )
            state["initial_boot_id"] = boot_id
            state["last_boot_id"] = boot_id

        _dispatch_next_reboot(state, now_fn=now_fn)
        payload = _progress_payload(state, settings, device_online=True, observed="reboot_sent")
        save_state(state, serial=serial)
        progress_stamp(payload)
        return {"success": True, "progress": payload}

    if phase == "WAIT_REBOOT":
        reboot_sent_at = float(state.get("reboot_sent_at") or now)
        elapsed = max(0.0, now - reboot_sent_at)
        ready, observed = system_ready()

        if not ready:
            if elapsed >= settings["boot_timeout_seconds"]:
                cycle = int(state.get("current_cycle", 1))
                return _fail_terminal(
                    state,
                    settings,
                    serial=serial,
                    reason_code="boot_timeout",
                    message=(
                        f"第 {cycle} 轮重启开机超时（状态={observed}，"
                        f"已等待 {elapsed:.1f}s >= {settings['boot_timeout_seconds']}s）"
                    ),
                    elapsed=elapsed,
                    now=now,
                    device_online=(observed != "offline"),
                    observed=observed,
                )
            payload = _progress_payload(
                state,
                settings,
                device_online=(observed != "offline"),
                observed=observed,
            )
            payload["boot_elapsed_s"] = round(elapsed, 1)
            save_state(state, serial=serial)
            progress_stamp(payload)
            return {"success": True, "progress": payload}

        cur_boot_id = read_boot_id()
        if not cur_boot_id:
            if elapsed >= settings["boot_timeout_seconds"]:
                cycle = int(state.get("current_cycle", 1))
                return _fail_terminal(
                    state,
                    settings,
                    serial=serial,
                    reason_code="boot_id_unreadable",
                    message=(
                        f"第 {cycle} 轮开机后持续无法读取内核 boot_id"
                        f"（已等待 {elapsed:.1f}s >= {settings['boot_timeout_seconds']}s）"
                    ),
                    elapsed=elapsed,
                    now=now,
                    device_online=True,
                    observed="boot_id_empty",
                )
            payload = _progress_payload(state, settings, device_online=True, observed="boot_id_retry")
            save_state(state, serial=serial)
            progress_stamp(payload)
            return {"success": True, "progress": payload}

        last_boot_id = str(state.get("last_boot_id") or "")
        if last_boot_id and cur_boot_id == last_boot_id:
            if elapsed >= settings["reboot_confirm_timeout_seconds"]:
                retries_used = int(state.get("reboot_retries_used", 0))
                max_retries = settings["reboot_retry_attempts"]
                if retries_used < max_retries:
                    retries_used += 1
                    rc, _, err = trigger_adb_reboot()
                    state["reboot_sent_at"] = now_fn()
                    state["reboot_retries_used"] = retries_used
                    state["reboot_retry_count"] = int(state.get("reboot_retry_count", 0)) + 1
                    state["last_reboot_rc"] = rc
                    state["last_reboot_err"] = err.strip()[:200] if err else ""
                    payload = _progress_payload(
                        state,
                        settings,
                        device_online=True,
                        observed=f"reboot_retry_{retries_used}",
                    )
                    payload["reboot_retries_used"] = retries_used
                    payload["reboot_retry_attempts"] = max_retries
                    save_state(state, serial=serial)
                    progress_stamp(payload)
                    return {"success": True, "progress": payload}

                cycle = int(state.get("current_cycle", 1))
                attempts = 1 + retries_used
                last_err = str(state.get("last_reboot_err") or "").strip()
                err_suffix = f", err={last_err!r}" if last_err else ""
                return _fail_terminal(
                    state,
                    settings,
                    serial=serial,
                    reason_code="reboot_not_effective",
                    message=(
                        f"第 {cycle} 轮 adb reboot 未生效（已尝试 {attempts} 次，"
                        f"{elapsed:.1f}s 后内核 boot_id 仍为 {cur_boot_id}，"
                        f"rc={state.get('last_reboot_rc')}{err_suffix}）"
                    ),
                    elapsed=elapsed,
                    now=now,
                    device_online=True,
                    observed="same_boot_id",
                )
            payload = _progress_payload(state, settings, device_online=True, observed="reboot_pending")
            payload["boot_elapsed_s"] = round(elapsed, 1)
            save_state(state, serial=serial)
            progress_stamp(payload)
            return {"success": True, "progress": payload}

        # boot_id 已变化 → 确认完成一次真实重启！
        state["reboot_retries_used"] = 0
        unlock_applied = unlock_to_home() if settings["unlock_to_home"] else False
        ready_at = now_fn()
        boot_duration_s = round(max(0.0, ready_at - reboot_sent_at), 2)
        cycle = int(state.get("current_cycle", 0)) or (int(state.get("cycles_done", 0)) + 1)

        entries = list(state.get("entries") or [])
        entries.append(
            {
                "cycle": cycle,
                "status": "PASS",
                "reboot_sent_at": format_ts(reboot_sent_at),
                "boot_completed_at": format_ts(ready_at),
                "boot_duration_s": boot_duration_s,
                "boot_id": cur_boot_id,
                "unlock_applied": unlock_applied,
            }
        )
        state["entries"] = entries
        state["cycles_done"] = int(state.get("cycles_done", 0)) + 1
        state["last_boot_id"] = cur_boot_id
        state["ready_at"] = ready_at

        target = settings["test_times"]
        if target > 0 and state["cycles_done"] >= target:
            state["phase"] = "COMPLETED"
            state["final_status"] = "PASS"
            state["finished_at"] = ready_at
            payload = _progress_payload(state, settings, device_online=True, observed="completed")
            payload["boot_duration_s"] = boot_duration_s
            payload["run_finished"] = True
            save_state(state, serial=serial)
            progress_stamp(payload)
            return {"success": True, "patrol_complete": True, "progress": payload}

        state["phase"] = "WAIT_INTERVAL"
        dispatched = _maybe_wait_and_reboot(
            state,
            settings,
            serial=serial,
            remaining=float(settings["wait_seconds"]),
            now_fn=now_fn,
            sleep_fn=sleep_fn,
        )
        payload = _progress_payload(
            state,
            settings,
            device_online=True,
            observed="reboot_sent_inline" if dispatched else "wait_interval",
        )
        payload["boot_duration_s"] = boot_duration_s
        save_state(state, serial=serial)
        progress_stamp(payload)
        return {"success": True, "progress": payload}

    if phase == "WAIT_INTERVAL":
        ready_at = float(state.get("ready_at") or now)
        waited = max(0.0, now - ready_at)
        remaining = float(settings["wait_seconds"]) - waited
        if remaining <= 0:
            _dispatch_next_reboot(state, now_fn=now_fn)
            payload = _progress_payload(state, settings, device_online=True, observed="reboot_sent")
            save_state(state, serial=serial)
            progress_stamp(payload)
            return {"success": True, "progress": payload}

        dispatched = _maybe_wait_and_reboot(
            state,
            settings,
            serial=serial,
            remaining=remaining,
            now_fn=now_fn,
            sleep_fn=sleep_fn,
        )
        payload = _progress_payload(
            state,
            settings,
            device_online=True,
            observed="reboot_sent_inline" if dispatched else "wait_interval",
        )
        if not dispatched:
            payload["interval_remaining_s"] = round(remaining, 1)
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
