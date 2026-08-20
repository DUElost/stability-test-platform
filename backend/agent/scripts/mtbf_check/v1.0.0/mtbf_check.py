# -*- coding: utf-8 -*-
"""MTBF 轮询（patrol 阶段，ADR-0030 D6 P0 / P0 设计 §3.4）。

每周期（patrol_interval_seconds，建议 300）执行一次：
1. 存活判定：RunTaskService 在跑？（设备端看门狗 30 分钟会自行拉起，连续 2 周期死亡才判死）
2. 进度采集：结果 XML 的 <testpoint 计数（带尾空格精确排除 <testpoints> 根元素）+ log 大小
3. PROGRESS 打戳（#115：stderr，配 stall_seconds 使用）
4. stdout JSON 摘要

跨周期状态（连续死亡计数）落在 Agent 本机临时文件，键 = 设备序列号。

STP_STEP_PARAMS:
{
    "project": "legacy",
    "expected_testpoint_count": 130,   // P0 人工预置（清单变更需同步）；0/缺失时只报绝对数
    "dead_grace_cycles": 2             // 连续 N 周期服务死亡判死
}

输出 (stdout): {"success": true/false, "metrics": {...}}
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from _lib import adb_shell, device_serial, output_result, params, progress_stamp

_OSM_PACKAGE = "com.ape.offlinescriptmanager"
_RESULTS_ROOT = "/sdcard/results"


def _state_file() -> Path:
    return Path(tempfile.gettempdir()) / f"mtbf_check_{device_serial()}.json"


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


def _service_alive() -> bool:
    out = adb_shell(f"dumpsys activity services {_OSM_PACKAGE}", timeout=30)
    return "RunTaskService" in out


def _latest_run_dir() -> str:
    """最新结果运行目录（时间戳目录名字典序即时间序）。"""
    out = adb_shell(f"ls {_RESULTS_ROOT}/realresult/", timeout=30).strip()
    names = [line for line in out.splitlines() if line.strip() and not line.startswith("total")]
    return names[-1] if names else ""


def _count_testpoints(run_dir: str) -> int:
    """设备端统计已完成 testpoint 条目数。

    用带尾空格的 `<testpoint ` 精确匹配（根元素 `<testpoints` 不匹配），
    grep -c 不可用时回退按文件大小估算（testcase 行数与 testpoint 同阶）。
    """
    xml = f"{_RESULTS_ROOT}/realresult/{run_dir}/TESTS-RealResult-TestPoints.xml"
    rc, out = _try_grep_count(xml)
    if rc == 0:
        try:
            return int(out.strip().splitlines()[-1])
        except ValueError:
            pass
    # 回退：文件存在性 + 大小（估算）
    ls = adb_shell(f"ls -l {xml}", timeout=30).strip()
    size = _parse_size_from_ls(ls)
    if size <= 0:
        return 0
    return max(1, size // 400)   # 单条 testpoint 序列化约 200~800B，量级参考


def _try_grep_count(xml: str) -> tuple[int, str]:
    """尝试 adb shell grep -c；返回 (rc, stdout)。"""
    rc, out, _ = _adb_grep(xml)
    return rc, out


def _adb_grep(xml: str) -> tuple[int, str, str]:
    import subprocess
    from _lib import adb_path
    try:
        result = subprocess.run(
            [adb_path(), "-s", device_serial(), "shell", f"grep -c '<testpoint ' {xml} 2>/dev/null"],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode, result.stdout or "", result.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"


def _parse_size_from_ls(ls: str) -> int:
    """从 ls -l 输出提取文件大小（busybox 变体字段不一，扫描首个纯数字 token）。"""
    if "No such file" in ls or not ls:
        return 0
    tokens = ls.split()
    size = next((t for t in tokens[2:] if t.isdigit()), None)
    if size is None:
        return 0
    try:
        return int(size)
    except ValueError:
        return 0


def _log_bytes(run_dir: str) -> int:
    ls = adb_shell(f"ls -l {_RESULTS_ROOT}/Log/{run_dir}/log.txt", timeout=30).strip()
    return _parse_size_from_ls(ls)


def _run(cfg: dict) -> dict:
    expected = int(cfg.get("expected_testpoint_count", 0) or 0)
    grace = int(cfg.get("dead_grace_cycles", 2) or 2)

    alive = _service_alive()
    run_dir = _latest_run_dir()
    done = _count_testpoints(run_dir) if run_dir else 0
    log_bytes = _log_bytes(run_dir) if run_dir else 0

    state = _load_state()
    if alive:
        state["dead_streak"] = 0
    else:
        state["dead_streak"] = int(state.get("dead_streak", 0)) + 1
    _save_state(state)

    dead = state["dead_streak"] >= max(1, grace)
    if dead:
        return {
            "success": False,
            "error_message": f"RunTaskService 连续 {state['dead_streak']} 个周期未存活（看门狗未恢复）",
            "progress": {
                "run_dir": run_dir,
                "testpoints_done": done,
                "expected_per_round": expected,
                "log_bytes": log_bytes,
            },
        }

    payload = {
        "seq": int(state.get("seq", 0)) + 1,
        "step": "mtbf_check",
        "run_dir": run_dir,
        "testpoints_done": done,
        "expected_per_round": expected,
        "log_bytes": log_bytes,
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
