"""Ensure device has root access via adb root.

v1.0.2（#2802 D1″，2026-09-21）：**重启撞峰吸收**——失败后先等设备**系统就绪**
（`get-state==device` 且 `sys.boot_completed==1`，判定与 `check_device` v1.0.2 /
`powercycle_finish` 同源）再重试，最多 `max_attempts`（默认 3）次、全程
`total_budget_seconds`（默认 150s）封顶。

依据：r477（首个 D1′ 窗）check_device 硬失败 0/吸收率 100% 后，剩余 107 条 init 失败里
`ensure_root` 占 48（39 no-root + 9 timeout），证据全部是
`adb_root rc=1 stderr="adb: unable to connect for root: device 'XXX' not found"`
——同一撞峰机制（设备被 PowerCycle 循环重启出 USB 视野），而 v1.0.1 的 3 次尝试
是**固定间隔、不带 boot 门**（boot 早期 adbd 短暂在线也照样打进去）。

判定语义**不变**（已是 root → skip；否则 `adb root` + `id -u`）；首次仍走**快速路径**
（正常设备耗时不变），仅重试前等待就绪。失败报文保留 v1.0.1 全部证据字段
（`adb_root rc=/stdout=/stderr=(+exc=) id_u= adb_state=`），另加 `attempts=` 与逐次
`history=`（每趟归类为 not_found/unauthorized/offline/closed/timeout/no_root/other）。

**步骤超时必须容纳 `total_budget_seconds`**（默认 150s；plan 54 现行 60s 需同步
放大到 ≥180s，属本期计划侧配套）。

Environment:
    STP_DEVICE_SERIAL   (required)
    STP_ADB_PATH        (default: adb)
    STP_STEP_PARAMS     (optional, JSON):
        {
          "max_attempts": 3,             // 含首次
          "wait_ready_seconds": 90,      // 每次重试前等系统就绪的上限
          "total_budget_seconds": 150,   // 全程预算（须 ≤ 步骤 timeout）
          "retry_delay_seconds": 3       // 每次尝试后的固定间隔
        }

Output (stdout):
    {"success": true/false, "skipped": bool, "error_message": "..."}
"""
from __future__ import annotations

import subprocess
import sys
import time

from _adb import adb_path, device_serial, output_result, params

_SNIPPET_LIMIT = 200
_STATE_TIMEOUT_SECONDS = 5
_ROOT_TIMEOUT_SECONDS = 10
_ID_TIMEOUT_SECONDS = 10
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


def _classify(stderr: str, exc: str, root_rc: int | None) -> str:
    """把一趟失败压成一个短标签（进 history，保持报文有界）。"""
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
    if root_rc == 0:
        return "no_root"  # adb root 报成功但 id -u != 0（如 userbuild 拒绝）
    return "other"


def _device_ready(serial: str, deadline: float) -> tuple[bool, str]:
    """设备是否**系统就绪**：get-state==device 且 sys.boot_completed==1。

    与 check_device v1.0.2 / powercycle_finish v1.0.2 同判定（boot 早期 adbd
    在线但系统未就绪）。返回 (是否就绪, 最后一次观测摘要)。
    """
    observed = ""
    while time.monotonic() < deadline:
        state = _adb_state(serial)
        observed = state
        if state.startswith("'device'"):
            try:
                proc = subprocess.run(
                    [adb_path(), "-s", serial, "shell", "getprop sys.boot_completed"],
                    capture_output=True, text=True,
                    timeout=min(15, max(1, int(deadline - time.monotonic()))),
                )
            except Exception as exc:  # noqa: BLE001
                observed = f"<{type(exc).__name__}>"
            else:
                if (proc.stdout or "").strip() == "1":
                    return True, observed
        time.sleep(min(_POLL_INTERVAL_SECONDS, max(1, deadline - time.monotonic())))
    return False, observed


def _id_u(serial: str, timeout: int = _ID_TIMEOUT_SECONDS) -> tuple[bool, str]:
    """读 `id -u`：返回 (是否 root, 原始读数片段)；异常返回 (False, '<ExcName>')。"""
    try:
        result = subprocess.run(
            [adb_path(), "-s", serial, "shell", "id -u"],
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"<{type(exc).__name__}>"
    out = _snippet(result.stdout, 40)
    return out.strip() == "0", out


def _adb_root(serial: str):
    """一次 `adb root`；异常以 (None, exc_name) 形式返回。"""
    try:
        return subprocess.run(
            [adb_path(), "-s", serial, "root"],
            capture_output=True, text=True, timeout=_ROOT_TIMEOUT_SECONDS,
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
    retry_delay = float(args.get("retry_delay_seconds", 3.0))

    is_root, id_u = _id_u(serial)
    if is_root:
        output_result(True, skipped=True, skip_reason="Already root")
        return

    deadline = time.monotonic() + total_budget_seconds
    history: list[str] = []
    waited_ready = False
    last_rc: int | None = None
    last_out = ""
    last_err = ""
    last_exc = ""

    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            remaining = deadline - time.monotonic()
            if remaining <= 1:
                break
            ready, _observed = _device_ready(serial, time.monotonic() + min(wait_ready_seconds, remaining))
            waited_ready = waited_ready or ready
            if not ready:
                history.append(f"{attempt}:not_ready")
                break

        proc, exc = _adb_root(serial)
        if proc is None:
            last_exc = exc
            history.append(f"{attempt}:{_classify('', exc, None)}")
            if attempt < max_attempts and deadline - time.monotonic() > retry_delay:
                time.sleep(retry_delay)
            continue

        last_rc = proc.returncode
        last_out = _snippet(proc.stdout)
        last_err = _snippet(proc.stderr)
        time.sleep(retry_delay)

        is_root, id_u = _id_u(serial)
        if is_root:
            output_result(True, metrics={"attempts": attempt, "waited_ready": waited_ready})
            return
        history.append(f"{attempt}:{_classify(last_err, '', last_rc)}")
        if attempt < max_attempts and deadline - time.monotonic() > retry_delay:
            time.sleep(retry_delay)

    evidence = (
        f"adb_root rc={last_rc} stdout={last_out!r} stderr={last_err!r}"
        + (f" exc={last_exc!r}" if last_exc else "")
        + f" id_u={id_u!r} adb_state={_adb_state(serial)}"
    )
    tail = f"attempts={len(history)}/{max_attempts} history=[{','.join(history)}] {evidence}"
    if last_exc and last_rc is None:
        output_result(
            False,
            error_message=f"adb root failed after {max_attempts} attempts: {last_exc} ({tail})",
        )
    else:
        output_result(
            False,
            error_message=f"Root access not granted after {max_attempts} attempts ({tail})",
        )
    sys.exit(1)


if __name__ == "__main__":
    main()
