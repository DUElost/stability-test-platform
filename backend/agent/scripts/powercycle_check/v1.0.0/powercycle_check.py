# -*- coding: utf-8 -*-
"""PowerCycle 轮询（patrol 阶段，issue #462 P0b；G15 对齐 §3.2）。

每周期（patrol_interval_seconds，建议 300）执行一次：
1. 存活判定：PowerCycleService 在跑？——**设备离线（重启周期中）不判服务死亡**
   （reboot 模式固有现象；持续离线由平台心跳 UNKNOWN/恢复链路兜底）；
   设备在线但服务死亡连续 dead_grace_cycles（默认 2）周期 → 判死。
2. 进度采集：prefs current_count/test_times（run-as 读，权威续跑计数）；
   prefs 读不到 → 设备端 grep -c 'cycle ' 兜底（结果文件行数）。
3. PROGRESS 打戳（#115：stderr，配 stall_seconds 使用）。
4. stdout JSON 摘要。

跨周期状态（连续死亡计数）落在 Agent 本机临时文件，键 = 设备序列号。

STP_STEP_PARAMS:
{
    "project": "legacy",
    "expected_cycles": 100,      // 可选注入（P1 suite 绑定后自动填充）；0=用 prefs test_times
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
    adb_path,
    adb_shell,
    device_online,
    device_serial,
    get_prefs_xml,
    output_result,
    param_or_env,
    params,
    parse_size_from_ls,
    progress_stamp,
    result_paths,
    service_alive,
)


def _state_file() -> Path:
    return Path(tempfile.gettempdir()) / f"powercycle_check_{device_serial()}.json"


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


def _read_prefs_progress() -> tuple[int, int] | None:
    """prefs current_count/test_times（run-as 读）；读不到返回 None。"""
    xml = get_prefs_xml()
    if not xml:
        return None
    cur = re.search(r'name="current_count" value="(\d+)"', xml)
    if not cur:
        return None
    total = re.search(r'name="test_times" value="(\d+)"', xml)
    return int(cur.group(1)), int(total.group(1)) if total else 0


def _grep_cycle_count() -> int:
    """兜底：结果文件 'cycle ' 行数（grep -c；多路径逐个试）。"""
    for path in result_paths():
        rc, out, _ = _adb_grep(path)
        if rc == 0 and out.strip():
            try:
                return int(out.strip().splitlines()[-1])
            except ValueError:
                pass
    return 0


def _result_bytes() -> int:
    for path in result_paths():
        ls = adb_shell(f"ls -l {path}", timeout=30).strip()
        size = parse_size_from_ls(ls)
        if size > 0:
            return size
    return 0


def _adb_grep(path: str) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            [adb_path(), "-s", device_serial(), "shell", f"grep -c 'cycle ' {path} 2>/dev/null"],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode, result.stdout or "", result.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"


def _run(cfg: dict) -> dict:
    grace = max(1, int(str(param_or_env(cfg, "dead_grace_cycles", "STP_POWER_CYCLE_DEAD_GRACE_CYCLES", "2")) or 2))
    injected = int(str(cfg.get("expected_cycles") or 0) or 0)

    state = _load_state()

    if not device_online():
        # 重启周期中设备离线属正常：不判服务死亡，也不推进 dead_streak。
        # 持续离线由平台心跳 UNKNOWN → grace → FAILED 链路处理。
        payload = {
            "seq": int(state.get("seq", 0)) + 1,
            "step": "powercycle_check",
            "cycles_done": 0,
            "expected_cycles": injected,
            "result_bytes": 0,
            "device_online": False,
            "service_alive": False,
        }
        state["seq"] = payload["seq"]
        _save_state(state)
        progress_stamp(payload)
        return {"success": True, "progress": payload}

    alive = service_alive()
    prefs = _read_prefs_progress()
    cycles_done = prefs[0] if prefs else _grep_cycle_count()
    expected = injected or (prefs[1] if prefs else 0)
    result_bytes = _result_bytes()

    if alive:
        state["dead_streak"] = 0
    else:
        state["dead_streak"] = int(state.get("dead_streak", 0)) + 1
    _save_state(state)

    if state["dead_streak"] >= grace:
        return {
            "success": False,
            "error_message": f"PowerCycleService 连续 {state['dead_streak']} 个周期未存活",
            "progress": {
                "cycles_done": cycles_done,
                "expected_cycles": expected,
                "result_bytes": result_bytes,
                "device_online": True,
                "service_alive": False,
            },
        }

    payload = {
        "seq": int(state.get("seq", 0)) + 1,
        "step": "powercycle_check",
        "cycles_done": cycles_done,
        "expected_cycles": expected,
        "result_bytes": result_bytes,
        "device_online": True,
        "service_alive": alive,
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
