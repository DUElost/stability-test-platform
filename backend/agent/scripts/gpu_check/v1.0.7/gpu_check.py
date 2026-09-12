# -*- coding: utf-8 -*-
"""GPU 轮询（patrol 阶段，issue #462 P0c；G15 对齐 §3.3）。

v1.0.6（#1028，R08-F10）：与 mtbf_check v1.4.0 同型——巡检状态写入``job_id``（STP_JOB_ID），新 Job 不继承上一 Job 的 dead_streak/seq。

v1.0.7（#831）：崩溃循环提前判失败——未到 GPU_RUN_END 时也检查日志里的
落败证据（``Process crashed`` / ``OK (0 tests)`` / 连续 N 轮 ``GPU_ROUND
rc<0``，N=crash_round_streak 默认 3），命中即当周期失败，不再等 700 轮
跑完。此前崩溃循环里 loop 脚本与 am 客户端始终存活、dead_streak 永不
触发，每周期报 success 且 rounds_done 上涨（平台视角「测试在推进」），
可空转数十分钟到数小时（v1.0.3 实测空跑即此形状）。

v1.0.2：test_log.txt 含 instrument 二进制 protobuf 输出——_run_finished 改
bytes 模式读取（text 解码抛 UnicodeDecodeError，冒烟发现 ⑤）。
v1.0.3：OK (0 tests) 空跑显式失败（2026-08-31 实证）。
v1.0.4：monitor 模式（-m）兼容——runner 只输出 protobuf 无 OK 文本，
正常完成（test_result=true）不再误判空跑（2026-09-01 全量实证）。

每周期（patrol_interval_seconds，建议 300）执行一次：
1. 存活判定：instrument 进程/循环脚本在跑？
   已出现 GPU_RUN_END（自然收尾）→ 报完成；
   未收尾但日志已有崩溃证据（v1.0.7）→ 提前报失败；
   未收尾且进程死 → 连续 dead_grace_cycles（默认 2）周期判死。
2. 进度采集：test_log.txt 的 GPU_ROUND 标记计数（grep）+ 日志大小。
3. PROGRESS 打戳（#115：stderr，配 stall_seconds 使用）。
4. stdout JSON 摘要。

跨周期状态（连续死亡计数）落在 Agent 本机临时文件，键 = 设备序列号。

STP_STEP_PARAMS:
{
    "project": "legacy",
    "expected_rounds": 700,     // 可选注入；0=用 GPU_RUN_START rounds 标记
    "dead_grace_cycles": 2,
    "crash_round_streak": 3     // v1.0.7：连续 N 轮 GPU_ROUND rc<0 判失败
}

输出 (stdout): {"success": true/false, "progress": {...}}
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from _lib import (
    _RESULT_LOG,
    adb_path,
    device_serial,
    instrument_alive,
    output_result,
    param_or_env,
    params,
    progress_stamp,
    result_log_bytes,
)


def _state_file() -> Path:
    return Path(tempfile.gettempdir()) / f"gpu_check_{device_serial()}.json"


def _load_state() -> dict:
    try:
        return json.loads(_state_file().read_text())
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        _state_file().write_text(json.dumps(state))
    except OSError:
        pass


def _grep_rounds_done() -> int:
    """设备端 grep -c '^GPU_ROUND ' test_log.txt（标记行行首锚定）。"""
    try:
        result = subprocess.run(
            [adb_path(), "-s", device_serial(), "shell", f"grep -c '^GPU_ROUND ' {_RESULT_LOG} 2>/dev/null"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            try:
                return int(result.stdout.strip().splitlines()[-1])
            except ValueError:
                pass
    except subprocess.TimeoutExpired:
        pass
    return 0


_TESTS_OK_RE = re.compile(rb"OK \((\d+) tests?\)")


def _last_protobuf_test_result(log: bytes) -> "bool | None":
    """protobuf（-m monitor 模式）里最后一个 test_result 字段的值。

    AndroidJUnitRunner 的 -m 模式只输出 protobuf，不打印 "OK (N tests)"
    文本（2026-09-01 全量实证）。protobuf 字段：``test_result\x12\x05true``
    （string 值 "true"/"false"），取最后一个（多轮时尾部为准）。
    """
    idx = log.rfind(b"test_result")
    if idx < 0:
        return None
    window = log[idx:idx + 64]
    if b"true" in window:
        return True
    if b"false" in window:
        return False
    return None


def _run_finished(log: "bytes | None" = None) -> "tuple[bool, str]":
    """test_log.txt 是否已有 GPU_RUN_END（自然收尾标记）+ 真实测试判定。

    v1.0.2：bytes 模式读取——test_log.txt 含 instrument 的二进制 protobuf
    输出，text=True 的 utf-8 解码会抛 UnicodeDecodeError（冒烟发现 ⑤）。

    v1.0.3：返回 (finished, verdict)。verdict:
      - "ok":        GPU_RUN_END 且 OK (N tests) N>0 —— 真实测试执行完成
      - "no-tests":  GPU_RUN_END 但 OK (0 tests) —— 空跑（2026-08-31 实证：
        APK 方法名不匹配时 am instrument 假成功，手机实际静置）
      - "running":   未到 GPU_RUN_END

    v1.0.4：补 monitor 模式兼容——-m 模式的 runner 只输出 protobuf，正常
    完成时没有 "OK (N tests)" 文本（test_result=true + testcase_name），
    v1.0.3 会把正常完成误判为空跑（2026-09-01 全量实证：同 APK 同方法，
    v1.0.2 时代判成功、v1.0.3 全报 OK (0 tests)）。判定顺序：
      1. GPU_RUN_END 缺失 → running
      2. "OK (N tests)" 文本（非 monitor）→ N>0 ok / N==0 no-tests
      3. "Process crashed" → crashed（instrument 进程崩溃）
      4. 最后一个 protobuf test_result=true → ok（monitor 正常完成）
      5. 其余 → no-tests

    v1.0.7：接受调用方已读的 *log*（同周期只 cat 一次；不传则自读）。
    """
    if log is None:
        log = _read_log_cat()
    if b"GPU_RUN_END" not in log:
        return False, "running"
    m = _TESTS_OK_RE.search(log)
    if m:
        return True, ("ok" if int(m.group(1)) > 0 else "no-tests")
    if b"FAILURES!!!" in log:
        # v1.0.5（#774 实证 run 353）：测试执行但 JUnit 失败（如
        # "antutu app start test | restart" AssertionError）——非空跑非崩溃，
        # 归因 "failed"（区别于 no-tests/crashed——修复 #746 类误归因）
        return True, "failed"
    if b"Process crashed" in log:
        return True, "crashed"
    if _last_protobuf_test_result(log) is True:
        return True, "ok"
    return True, "no-tests"


_ROUND_MARKER_RE = re.compile(rb"^GPU_ROUND (\d+) rc=(-?\d+)$", re.M)


def _early_crash_verdict(
    log: bytes, *, rc_streak: int,
) -> "tuple[str, str] | None":
    """v1.0.7（#831）：未到 GPU_RUN_END 的崩溃循环证据 → 提前判失败。

    返回 (kind, error_message)；无证据返回 None。判据（任一命中）：
      1. ``Process crashed``——instrument 崩溃，文本判定不需要等 END；
      2. 最后一个 ``OK (0 tests)``——空跑（方法名/variant 不匹配）。取最后
         一个而非首个：中途单轮空跑后恢复不误杀；
      3. 最近连续 ``rc_streak`` 轮 ``GPU_ROUND rc<0``——每轮崩溃、测试未推进。
    """
    if b"Process crashed" in log:
        return "crashed", (
            "GPU instrument 进程崩溃（Process crashed）——测试未完成、"
            "未见 GPU_RUN_END（v1.0.7 提前判定），见 test_log.txt shortMsg"
        )
    ok_matches = list(_TESTS_OK_RE.finditer(log))
    if ok_matches and int(ok_matches[-1].group(1)) == 0:
        return "no-tests", (
            "GPU 空跑：OK (0 tests)——测试方法未执行、未见 GPU_RUN_END"
            "（v1.0.7 提前判定；APK 方法名/variant 不匹配，见 2026-08-31 实证）"
        )
    rounds = _ROUND_MARKER_RE.findall(log)
    if rc_streak > 0 and len(rounds) >= rc_streak:
        if all(int(rc) < 0 for _n, rc in rounds[-rc_streak:]):
            return "round-crash-loop", (
                f"GPU 连续 {rc_streak} 轮崩溃（GPU_ROUND rc<0）——测试未推进、"
                "未见 GPU_RUN_END（v1.0.7 提前判定）"
            )
    return None


def _read_log_cat() -> bytes:
    try:
        result = subprocess.run(
            [adb_path(), "-s", device_serial(), "shell", f"cat {_RESULT_LOG}"],
            capture_output=True, timeout=30,
        )
        return result.stdout or b""
    except subprocess.TimeoutExpired:
        return b""


def _run(cfg: dict) -> dict:
    grace = max(1, int(str(param_or_env(cfg, "dead_grace_cycles", "STP_GPU_DEAD_GRACE_CYCLES", "2")) or 2))
    injected = int(str(cfg.get("expected_rounds") or 0) or 0)

    state = _load_state()
    # #1028（R08-F10）：巡检状态是「单次运行」语义——新 Job 不继承上一 Job 的
    # dead_streak / 周期序号 / 收取窗口标记，否则新 Job 首个周期就背上旧账、
    # 提前耗尽宽限。job 身份来自 Agent 注入的 STP_JOB_ID；身份缺失（手动跑）
    # 时与残留旧键不等同样重置一次，此后维持既有语义。
    job_id = os.environ.get("STP_JOB_ID", "").strip()
    if state.get("job_id") != job_id:
        state = {"job_id": job_id}
        _save_state(state)

    alive = instrument_alive()
    done = _grep_rounds_done()
    log_bytes = result_log_bytes()
    log = _read_log_cat()
    finished, verdict = _run_finished(log)

    if not finished:
        # v1.0.7（#831）：崩溃循环提前判失败——loop 脚本与 am 客户端存活时
        # dead_streak 永不触发，只靠 END 后判定会空转数十分钟到数小时。
        rc_streak = max(
            0,
            int(str(param_or_env(
                cfg, "crash_round_streak", "STP_GPU_CRASH_ROUND_STREAK", "3",
            )) or 3),
        )
        early = _early_crash_verdict(log, rc_streak=rc_streak)
        if early is not None:
            early_kind, early_message = early
            return {
                "success": False,
                "error_message": early_message,
                "progress": {
                    "rounds_done": done,
                    "expected_rounds": injected,
                    "log_bytes": log_bytes,
                    "instrument_alive": alive,
                    "run_finished": False,
                    "early_crash": early_kind,
                },
            }

    if finished and verdict == "no-tests":
        # v1.0.3：空跑显式失败——GPU_RUN_END 但 0 tests（方法名/variant 不匹配）
        return {"success": False, "error_message": (
            "GPU 空跑：OK (0 tests)——测试方法未执行，手机实际静置"
            "（APK 方法名/variant 不匹配，见 2026-08-31 实证）")}

    if finished and verdict == "crashed":
        # v1.0.4：instrument 进程崩溃（shortMsg: Process crashed.）
        return {"success": False, "error_message": (
            "GPU instrument 进程崩溃（Process crashed）——测试未完成，"
            "见 test_log.txt shortMsg")}

    if finished and verdict == "failed":
        # v1.0.5：测试执行但 JUnit 失败（FAILURES!!!——antutu app 启动等断言）
        return {"success": False, "error_message": (
            "GPU 测试执行但 JUnit 失败（FAILURES——如 antutu app 启动失败），"
            "见 test_log.txt FAILURES 详情")}

    if finished:
        # 自然收尾：报完成（success 与否由 teardown 解析 rc 定）
        payload = {
            "seq": int(state.get("seq", 0)) + 1,
            "step": "gpu_check",
            "rounds_done": done,
            "expected_rounds": injected,
            "log_bytes": log_bytes,
            "instrument_alive": False,
            "run_finished": True,
        }
        state["seq"] = payload["seq"]
        _save_state(state)
        progress_stamp(payload)
        return {"success": True, "progress": payload}

    if alive:
        state["dead_streak"] = 0
    else:
        state["dead_streak"] = int(state.get("dead_streak", 0)) + 1
    _save_state(state)

    if state["dead_streak"] >= grace:
        return {
            "success": False,
            "error_message": f"GPU 压测进程连续 {state['dead_streak']} 个周期未存活且无 GPU_RUN_END",
            "progress": {
                "rounds_done": done,
                "expected_rounds": injected,
                "log_bytes": log_bytes,
                "instrument_alive": False,
                "run_finished": False,
            },
        }

    payload = {
        "seq": int(state.get("seq", 0)) + 1,
        "step": "gpu_check",
        "rounds_done": done,
        "expected_rounds": injected,
        "log_bytes": log_bytes,
        "instrument_alive": alive,
        "run_finished": False,
    }
    state["seq"] = payload["seq"]
    _save_state(state)
    progress_stamp(payload)
    return {"success": True, "progress": payload}


def main() -> None:
    cfg = params()
    try:
        result = _run(cfg)
    except Exception as exc:  # noqa: BLE001
        output_result(False, error_message=str(exc))
        sys.exit(1)
    success = bool(result.pop("success"))
    output_result(success, **result)


if __name__ == "__main__":
    main()
