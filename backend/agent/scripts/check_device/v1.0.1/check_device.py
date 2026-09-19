"""Check whether device is reachable via ADB.

v1.0.1（#2802，2026-09-19）：**失败诊断**——`adb shell "echo test"` 的
stdout/stderr/exit code 与 `adb get-state` 结果不再被丢弃。v1.0.0 的失败只写
``unexpected output``，使 2026-09-19 开关机窗 init 失败波（check_device ×98，
真实窗口 T+0~3min、host 慢性，见 docs/notes/bug-fix/2026-09-19-powercycle-wave-
mechanism-frame-2802.md）在库内无法区分「adb 断连（offline/unauthorized/closed）」
与「shell 返回乱码」两类成因，只能依赖窗内人工抓取。

判定语义**不变**（仍是 v1.0.0 的 ``"test" in stdout`` 与 expect_root 检查），
仅失败报文携带证据；字段顺序固定（rc/stdout/stderr/adb_state）便于 grep 聚合。

Environment:
    STP_DEVICE_SERIAL  (required)
    STP_ADB_PATH       (default: adb)
    STP_STEP_PARAMS    (optional, JSON: {expect_root: bool})

Output (stdout):
    {"success": true/false, "error_message": "..."}
"""
from __future__ import annotations

import subprocess
import sys

from _adb import adb_path, device_serial, output_result, params

#: 单字段截断长度：够看清「device offline / Failure[...] / banner 文本」，
#: 又不会把 step_trace.error_message 撑爆（一条消息最多 ~600 字符）。
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
    """`adb get-state` 的一行摘要——失败时报文里区分 offline/unauthorized/断连。"""
    try:
        proc = subprocess.run(
            [adb_path(), "-s", serial, "get-state"],
            capture_output=True, text=True, timeout=_STATE_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 — 诊断字段不得反过来把判定打挂
        return f"<{type(exc).__name__}>"
    state = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    return f"{_snippet(state, 80)!r} rc={proc.returncode}"


def main() -> None:
    serial = device_serial()
    args = params()

    try:
        result = subprocess.run(
            [adb_path(), "-s", serial, "shell", "echo test"],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        output_result(
            False,
            error_message=(
                f"Device {serial} unreachable: timeout "
                f"(stdout={_snippet(exc.stdout)!r} stderr={_snippet(exc.stderr)!r} "
                f"adb_state={_adb_state(serial)})"
            ),
        )
        sys.exit(1)
    except Exception as exc:
        output_result(False, error_message=f"Device {serial} unreachable: {exc}")
        sys.exit(1)

    if "test" not in (result.stdout or ""):
        output_result(
            False,
            error_message=(
                f"Device {serial} check failed: unexpected output "
                f"(rc={result.returncode} stdout={_snippet(result.stdout)!r} "
                f"stderr={_snippet(result.stderr)!r} adb_state={_adb_state(serial)})"
            ),
        )
        sys.exit(1)

    if args.get("expect_root"):
        root_check = subprocess.run(
            [adb_path(), "-s", serial, "shell", "id -u"],
            capture_output=True, text=True, timeout=10,
        )
        if (root_check.stdout or "").strip() != "0":
            output_result(False, error_message=f"Device {serial} has no root access")
            sys.exit(1)

    output_result(True, serial=serial, skipped=False)


if __name__ == "__main__":
    main()
