# -*- coding: utf-8 -*-
"""GPU 轮询（patrol 阶段，issue #462 P0c；G15 对齐 §3.3）。

v1.0.2：test_log.txt 含 instrument 二进制 protobuf 输出——_run_finished 改
bytes 模式读取（text 解码抛 UnicodeDecodeError，冒烟发现 ⑤）。

每周期（patrol_interval_seconds，建议 300）执行一次：
1. 存活判定：instrument 进程/循环脚本在跑？
   已出现 GPU_RUN_END（自然收尾）→ 报完成；
   未收尾且进程死 → 连续 dead_grace_cycles（默认 2）周期判死。
2. 进度采集：test_log.txt 的 GPU_ROUND 标记计数（grep）+ 日志大小。
3. PROGRESS 打戳（#115：stderr，配 stall_seconds 使用）。
4. stdout JSON 摘要。

跨周期状态（连续死亡计数）落在 Agent 本机临时文件，键 = 设备序列号。

STP_STEP_PARAMS:
{
    "project": "legacy",
    "expected_rounds": 700,     // 可选注入；0=用 GPU_RUN_START rounds 标记
    "dead_grace_cycles": 2
}

输出 (stdout): {"success": true/false, "progress": {...}}
"""
from __future__ import annotations

import json
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


def _run_finished() -> "tuple[bool, str]":
    """test_log.txt 是否已有 GPU_RUN_END（自然收尾标记）+ 真实测试判定。

    v1.0.2：bytes 模式读取——test_log.txt 含 instrument 的二进制 protobuf
    输出，text=True 的 utf-8 解码会抛 UnicodeDecodeError（冒烟发现 ⑤）。

    v1.0.3：返回 (finished, verdict)。verdict:
      - "ok":        GPU_RUN_END 且 OK (N tests) N>0 —— 真实测试执行完成
      - "no-tests":  GPU_RUN_END 但 OK (0 tests) —— 空跑（2026-08-31 实证：
        APK 方法名不匹配时 am instrument 假成功，手机实际静置）
      - "running":   未到 GPU_RUN_END
    """
    log = _read_log_cat()
    if b"GPU_RUN_END" not in log:
        return False, "running"
    m = _TESTS_OK_RE.search(log)
    if not m or int(m.group(1)) == 0:
        return True, "no-tests"
    return True, "ok"


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
    alive = instrument_alive()
    done = _grep_rounds_done()
    log_bytes = result_log_bytes()
    finished, verdict = _run_finished()

    if finished and verdict == "no-tests":
        # v1.0.3：空跑显式失败——GPU_RUN_END 但 0 tests（方法名/variant 不匹配）
        return {"success": False, "error_message": (
            "GPU 空跑：OK (0 tests)——测试方法未执行，手机实际静置"
            "（APK 方法名/variant 不匹配，见 2026-08-31 实证）")}

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
