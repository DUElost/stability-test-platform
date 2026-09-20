"""Ensure device has root access via adb root.

v1.0.1（#2802 配套，2026-09-20）：**失败证据字段**——失败报文带上 `adb root` 的
rc/stdout/stderr、`id -u` 的原始读数与 `adb get-state` 摘要。v1.0.0 只写
``Root access not granted after N attempts``，使 2026-09-20 五窗复盘时无法区分
「adbd 拒绝 root（不可调试固件）」与「瞬时扰动打在 adb root/id -u 上」——
实测 147 台失败里 146 台是后者（同窗 check_device 均已通过、`ro.debuggable=1`），
唯一的确定性台是 `ro.debuggable=0` 的坏固件批次（#2753）。

判定语义**不变**（已是 root → skip；否则最多 max_attempts 次 `adb root` + `id -u`），
仅失败报文携带证据；字段顺序固定 `adb_root rc=/stdout=/stderr=/exc= id_u= adb_state=`。

Environment:
    STP_DEVICE_SERIAL   (required)
    STP_ADB_PATH        (default: adb)
    STP_STEP_PARAMS     (optional, JSON: {max_attempts: int, retry_delay_seconds: float})

Output (stdout):
    {"success": true/false, "skipped": bool, "error_message": "..."}
"""

import subprocess
import sys
import time

from _adb import adb_path, device_serial, output_result, params

_SNIPPET_LIMIT = 200
_STATE_TIMEOUT_SECONDS = 5


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


def _adb_state(serial: str) -> str:
    """`adb get-state` 的一行摘要——失败时区分 offline/unauthorized/断连。"""
    try:
        proc = subprocess.run(
            [adb_path(), "-s", serial, "get-state"],
            capture_output=True, text=True, timeout=_STATE_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 — 诊断字段不得反过来把判定打挂
        return f"<{type(exc).__name__}>"
    state = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    return f"{_snippet(state, 80)!r} rc={proc.returncode}"


def _id_u(serial: str, timeout: int = 10) -> tuple[bool, str]:
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


def main() -> None:
    serial = device_serial()
    args = params()
    max_attempts = args.get("max_attempts", 3)
    retry_delay = args.get("retry_delay_seconds", 3.0)

    is_root, id_u = _id_u(serial)
    if is_root:
        output_result(True, skipped=True, skip_reason="Already root")
        return

    last_rc: int | None = None
    last_out = ""
    last_err = ""
    last_exc = ""
    for attempt in range(1, max_attempts + 1):
        try:
            proc = subprocess.run(
                [adb_path(), "-s", serial, "root"],
                capture_output=True, text=True, timeout=10,
            )
            last_rc = proc.returncode
            last_out = _snippet(proc.stdout)
            last_err = _snippet(proc.stderr)
            time.sleep(retry_delay)

            is_root, id_u = _id_u(serial)
            if is_root:
                output_result(True, metrics={"attempts": attempt})
                return
        except Exception as exc:  # noqa: BLE001
            last_exc = f"{type(exc).__name__}: {exc}"
            if attempt == max_attempts:
                break
            time.sleep(2)

    evidence = (
        f"adb_root rc={last_rc} stdout={last_out!r} stderr={last_err!r}"
        + (f" exc={last_exc!r}" if last_exc else "")
        + f" id_u={id_u!r} adb_state={_adb_state(serial)}"
    )
    if last_exc and last_rc is None:
        output_result(
            False,
            error_message=f"adb root failed after {max_attempts} attempts: {last_exc} ({evidence})",
        )
    else:
        output_result(
            False,
            error_message=f"Root access not granted after {max_attempts} attempts ({evidence})",
        )
    sys.exit(1)


if __name__ == "__main__":
    main()
