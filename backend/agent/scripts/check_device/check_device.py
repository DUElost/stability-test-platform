"""Check whether device is reachable via ADB.

v1.0.2（#2802 D1′，2026-09-20）：**重启撞峰吸收**——失败即重试，重试前等设备
**系统就绪**（`get-state==device` 且 `sys.boot_completed==1`，判定与
`powercycle_finish` v1.0.2 发现⑪ 同源）。v1.0.1 只在失败时报证据：r457 实测
172 个 init 失败里 100 条是 `check_device` 的 `adb: device '<serial>' not found`
——设备正被 PowerCycle 循环重启（每 ~75s 一次），命令落进重启窗口即「不存在」。
本版把「等回来再试」接上（先例：`powercycle_finish` v1.0.4 的
`collect_attempts` + `wait_device_online_seconds`，同一把伞）。

判定语义**不变**（仍要求 `adb shell "echo test"` 返回含 `test`；`expect_root`
时 `id -u==0`）；首次仍走**快速路径**（不等就绪），仅重试前等待——正常设备
耗时不变。失败报文保留 v1.0.1 全部证据字段，另加 `attempts=` 与逐次
`history=`（每次失败归类为 not_found/offline/unauthorized/closed/timeout/other）。

**步骤超时必须容纳 `total_budget_seconds`**（默认 150s；plan 54 现行 30s 需同步
放大到 ≥180s，属本期计划侧配套）。

Environment:
    STP_DEVICE_SERIAL  (required)
    STP_ADB_PATH       (default: adb)
    STP_STEP_PARAMS    (optional, JSON):
        {
          "expect_root": false,
          "max_attempts": 3,             // 含首次
          "wait_ready_seconds": 90,      // 每次重试前等系统就绪的上限
          "total_budget_seconds": 150,   // 全程预算（须 ≤ 步骤 timeout）
          "retry_delay_seconds": 5       // 两次尝试之间的固定间隔
        }

Output (stdout):
    {"success": true/false, "error_message": "..."}
"""
from __future__ import annotations

import subprocess
import sys
import time

from _adb import adb_path, device_serial, output_result, params

_SNIPPET_LIMIT = 200
_STATE_TIMEOUT_SECONDS = 5
_ECHO_TIMEOUT_SECONDS = 10
_POLL_INTERVAL_SECONDS = 10


def _as_text(raw: object) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def _snippet(raw: object, limit: int = _SNIPPET_LIMIT) -> str:
    """单行化 + 截断（超限追加 …，一眼可见被截断）。"""
    text = " ".join(_as_text(raw).split())
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _adb_state(serial: str, timeout: int = _STATE_TIMEOUT_SECONDS) -> str:
    """`adb get-state` 的一行摘要——失败时区分 offline/unauthorized/断连。"""
    try:
        proc = subprocess.run(
            [adb_path(), "-s", serial, "get-state"],
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 — 诊断字段不得反过来把判定打挂
        return f"<{type(exc).__name__}>"
    state = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    return f"{_snippet(state, 80)!r} rc={proc.returncode}"


def _classify(stderr: str, exc: str) -> str:
    """把一次失败压成一个短标签（进 history，保持报文有界）。"""
    if exc:
        return "timeout" if "Timeout" in exc else "other"
    se = stderr.lower()
    if "not found" in se:
        return "not_found"
    if "unauthorized" in se:
        return "unauthorized"
    if "offline" in se:
        return "offline"
    if "closed" in se:
        return "closed"
    return "other"


def _device_ready(serial: str, deadline: float) -> tuple[bool, str]:
    """设备是否**系统就绪**：get-state==device 且 sys.boot_completed==1。

    与 powercycle_finish v1.0.2 同判定（boot 早期 adbd 在线但系统未就绪）。
    返回 (是否就绪, 最后一次观测摘要)。
    """
    observed = ""
    while time.monotonic() < deadline:
        state = _adb_state(serial)
        observed = state
        if state.startswith("'device'"):
            try:
                proc = subprocess.run(
                    [adb_path(), "-s", serial, "shell", "getprop sys.boot_completed"],
                    capture_output=True, text=True, timeout=min(15, max(1, int(deadline - time.monotonic()))),
                )
            except Exception as exc:  # noqa: BLE001
                observed = f"<{type(exc).__name__}>"
            else:
                if (proc.stdout or "").strip() == "1":
                    return True, observed
        time.sleep(min(_POLL_INTERVAL_SECONDS, max(1, deadline - time.monotonic())))
    return False, observed


def _run_echo(serial: str):
    """一次 `adb shell "echo test"`；异常以 (None, exc_name) 形式返回。"""
    try:
        return subprocess.run(
            [adb_path(), "-s", serial, "shell", "echo test"],
            capture_output=True, text=True, timeout=_ECHO_TIMEOUT_SECONDS,
        ), ""
    except subprocess.TimeoutExpired:
        return None, "TimeoutExpired"
    except Exception as exc:  # noqa: BLE001
        return None, type(exc).__name__


def main() -> None:
    serial = device_serial()
    args = params()
    max_attempts = int(args.get("max_attempts", 3))
    wait_ready_seconds = float(args.get("wait_ready_seconds", 90))
    total_budget_seconds = float(args.get("total_budget_seconds", 150))
    retry_delay = float(args.get("retry_delay_seconds", 5))

    deadline = time.monotonic() + total_budget_seconds
    history: list[str] = []
    result = None
    exc_name = ""
    waited_ready = False

    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            remaining = deadline - time.monotonic()
            if remaining <= 1:
                break
            ready, observed = _device_ready(serial, time.monotonic() + min(wait_ready_seconds, remaining))
            waited_ready = waited_ready or ready
            if not ready:
                history.append(f"{attempt}:not_ready")
                break
        result, exc_name = _run_echo(serial)
        if result is not None and "test" in (result.stdout or ""):
            if args.get("expect_root"):
                root_check = subprocess.run(
                    [adb_path(), "-s", serial, "shell", "id -u"],
                    capture_output=True, text=True, timeout=10,
                )
                if (root_check.stdout or "").strip() != "0":
                    output_result(False, error_message=f"Device {serial} has no root access")
                    sys.exit(1)
            output_result(True, serial=serial, skipped=False, metrics={
                "attempts": attempt, "waited_ready": waited_ready,
            })
            return
        history.append(f"{attempt}:{_classify((result.stderr or '') if result else '', exc_name)}")
        remaining = deadline - time.monotonic()
        if attempt < max_attempts and remaining > retry_delay:
            time.sleep(retry_delay)

    if result is None:
        evidence = f"adb_state={_adb_state(serial)}"
        head = f"Device {serial} unreachable: {exc_name or 'error'}"
    else:
        evidence = (
            f"rc={result.returncode} stdout={_snippet(result.stdout)!r} "
            f"stderr={_snippet(result.stderr)!r} adb_state={_adb_state(serial)}"
        )
        head = f"Device {serial} check failed: unexpected output"
    output_result(
        False,
        error_message=(
            f"{head} (attempts={len(history)}/{max_attempts} history=[{','.join(history)}] {evidence})"
        ),
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
