# -*- coding: utf-8 -*-
"""PowerCycle 轮询（patrol 阶段，issue #462 P0b；G15 对齐 §3.2）。

v1.0.5（综合验收方案 A）：**定时收取窗口**——``collect_window_start``
（"HH:MM"，**东八区业务时间**）+ ``collect_window_minutes``（10/30/60）Per-Plan。
⑥ boot 判死补强（v1.0.4→v1.0.5）：cycles_done==0 **或 result_bytes==0**
（boot 中 sdcard 未就绪，结果文件不可读）不累计 dead_streak——实测第 10 轮
boot 窗口（cycles>0 且未被离线观测）仍误判，result_bytes==0 是 boot 窗口
的可靠特征。
可配（步骤 params 自由键）；窗口内暂停任务 → 等设备稳定在线 → 收取结果写中心
存储 → 恢复续跑，PROGRESS 报 ``phase=collecting``。另修复验收发现⑥：设备
offline→online 转换（boot 窗口）不累计 dead_streak。

每周期（patrol_interval_seconds，建议 300）执行一次：
1. 存活判定：PowerCycleService 在跑？——**设备离线（重启周期中）不判服务死亡**
   （reboot 模式固有现象；持续离线由平台心跳 UNKNOWN/恢复链路兜底）；
   设备在线但服务死亡连续 dead_grace_cycles（默认 2）周期 → 判死（boot 转换除外）。
2. **完成检测（v1.0.1，同 sleep_check 冒烟发现）**：结果文件含 ``finished result=`` 行
   即测试自然收尾（服务完成 test_times 后自停）——优先报完成，不判死。
3. **定时收取窗口（v1.0.2）**：见上。
4. 进度采集：prefs current_count/test_times（run-as 读，权威续跑计数）；
   prefs 读不到 → 设备端 grep -c 'cycle ' 兜底（结果文件行数）。
5. PROGRESS 打戳（#115：stderr，配 stall_seconds 使用）。
6. stdout JSON 摘要。

跨周期状态（连续死亡计数）落在 Agent 本机临时文件，键 = 设备序列号。

STP_STEP_PARAMS:
{
    "project": "legacy",
    "expected_cycles": 100,      // 可选注入（P1 suite 绑定后自动填充）；0=用 prefs test_times
    "dead_grace_cycles": 2,
    "collect_window_start": "00:00",   // 定时收取窗口起点（Agent 本地时间；空=不启用）
    "collect_window_minutes": 30       // 窗口时长（10/30/60）
}

输出 (stdout): {"success": true/false, "progress": {...}}
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _lib import (
    adb_path,
    adb_shell,
    collect_powercycle_result,
    device_online,
    device_serial,
    get_prefs_xml,
    output_result,
    param_or_env,
    params,
    parse_size_from_ls,
    pause_task,
    progress_stamp,
    result_paths,
    resume_task,
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


def _run_finished() -> bool:
    """结果文件是否已有 ``finished result=`` 行（测试自然收尾标记）。"""
    for path in result_paths():
        try:
            result = subprocess.run(
                [adb_path(), "-s", device_serial(), "shell", f"cat {path}"],
                capture_output=True, text=True, timeout=30,
            )
        except subprocess.TimeoutExpired:
            continue
        if "finished result=" in (result.stdout or ""):
            return True
    return False


def _in_collect_window(cfg: dict, now=None) -> bool:
    """定时收取窗口判定（方法 A，验收方案确认）。

    ``collect_window_start`` = "HH:MM"（**Agent 主机本地时间**——设备时钟
    严重偏斜，不能读设备时间）；``collect_window_minutes`` = 10/30/60。
    窗口跨天（如 23:50 + 30min → 次日 00:20 前）正确覆盖。未配置 start
    （空）→ 恒 False（不启用）。
    """
    start = str(param_or_env(cfg, "collect_window_start", "STP_POWER_CYCLE_COLLECT_WINDOW_START", ""))
    if not start:
        return False
    minutes = max(1, int(str(param_or_env(
        cfg, "collect_window_minutes", "STP_POWER_CYCLE_COLLECT_WINDOW_MINUTES", "30",
    )) or 30))
    try:
        hh, mm = (int(x) for x in start.split(":"))
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return False
    except ValueError:
        return False
    # v1.0.3：窗口按**东八区（UTC+8）**判定——collect_window_start 是业务时区
    # （北京时间）配置；主机时区各异（实测 -67 为 PDT -07:00），主机本地
    # 时间判定会让「凌晨 0 点」在不同主机上错位 15 小时。
    now = now or datetime.now(timezone(timedelta(hours=8)))
    start_today = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if start_today <= now:
        return now < start_today + timedelta(minutes=minutes)
    # 起点在明天：昨天窗口可能跨到今天凌晨
    return now < (start_today - timedelta(days=1)) + timedelta(minutes=minutes)


def _wait_online_short(timeout_s: int) -> bool:
    """窗口收取前的短等待：设备稳定在线（收取必须在非重启窗口执行）。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if device_online():
            return True
        time.sleep(5)
    return False


def _run(cfg: dict) -> dict:
    grace = max(1, int(str(param_or_env(cfg, "dead_grace_cycles", "STP_POWER_CYCLE_DEAD_GRACE_CYCLES", "2")) or 2))
    injected = int(str(cfg.get("expected_cycles") or 0) or 0)
    project = str(param_or_env(cfg, "project", "STP_POWER_CYCLE_PROJECT", "legacy"))

    state = _load_state()

    if not device_online():
        # 重启周期中设备离线属正常：不判服务死亡，也不推进 dead_streak。
        # 持续离线由平台心跳 UNKNOWN → grace → FAILED 链路处理。
        state["last_online"] = False
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

    was_online = bool(state.get("last_online", True))

    if _in_collect_window(cfg):
        # 定时收取窗口（方法 A）：暂停 → 等设备稳定 → 收取 → 恢复续跑。
        # 窗口期间任务暂停中，服务死亡属预期——不判死。
        if not state.get("collecting_done_for_window"):
            try:
                pause_task()
                if not _wait_online_short(timeout_s=120):
                    raise RuntimeError("收取窗口内设备 120s 未上线，跳过本轮收取（下个周期重试）")
                collected = collect_powercycle_result(project)
                resume_task()
                state["last_collected"] = collected["run_id"]
                state["collecting_done_for_window"] = True
                state.pop("collect_error", None)
            except Exception as exc:  # noqa: BLE001 — 收取失败不判死，下周期重试
                state["collect_error"] = str(exc)
        payload = {
            "seq": int(state.get("seq", 0)) + 1,
            "step": "powercycle_check",
            "phase": "collecting",
            "cycles_done": 0,
            "expected_cycles": injected,
            "result_bytes": 0,
            "device_online": True,
            "service_alive": False,
            "last_collected_run_id": state.get("last_collected"),
            "collect_error": state.get("collect_error"),
        }
        state["seq"] = payload["seq"]
        _save_state(state)
        progress_stamp(payload)
        return {"success": True, "progress": payload}

    # 窗口外：清窗口状态
    state["collecting_done_for_window"] = False
    state.pop("collect_error", None)

    alive = service_alive()
    prefs = _read_prefs_progress()
    cycles_done = prefs[0] if prefs else _grep_cycle_count()
    expected = injected or (prefs[1] if prefs else 0)
    result_bytes = _result_bytes()

    if _run_finished():
        # 自然收尾：报完成（成败由 teardown 解析 final_status 定）
        payload = {
            "seq": int(state.get("seq", 0)) + 1,
            "step": "powercycle_check",
            "cycles_done": cycles_done,
            "expected_cycles": expected,
            "result_bytes": result_bytes,
            "device_online": True,
            "service_alive": alive,
            "run_finished": True,
        }
        state["seq"] = payload["seq"]
        state["last_online"] = True
        _save_state(state)
        progress_stamp(payload)
        return {"success": True, "progress": payload}

    # ⑥ 修复（验收发现，v1.0.4 补强）：boot 窗口不累计 dead_streak——
    # 重启周期 ~75s > patrol 60s，boot 窗口（adb 在线、服务未起）可跨 2 个
    # check。三个不计数条件：
    #   1. 服务在跑（正常）
    #   2. cycles_done == 0：测试刚开始的首个 boot 窗口（v1.0.3 只靠转换
    #      检测，重启间隙若未被离线 check 观测到就漏判——实测仍判死）
    #   3. offline→online 转换：后续轮次的 boot 窗口（服务未起属预期）
    if alive or cycles_done == 0 or result_bytes == 0 or not was_online:
        state["dead_streak"] = 0
    else:
        state["dead_streak"] = int(state.get("dead_streak", 0)) + 1
    state["last_online"] = True
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
