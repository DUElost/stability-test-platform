"""#2980：finish 族墙钟 SIGTERM 下临时目录必须被回收（handler + 启动兜底清扫）。

缺陷（v1.0.6/v1.0.5/v1.0.3 的 #2834 修复未堵全）：回收挂在 ``main()`` 的
``finally``，而步骤墙钟到点时引擎对进程组直接 SIGTERM（CPython 不转异常 ⇒
finally 不执行，实测退出码 -15），升级 SIGKILL 更无解——每次踩中即泄漏一个
``<family>-results-*`` 目录（宿主 /tmp 为 tmpfs）。

本版锁定（gpu_finish v1.0.7 / powercycle_finish v1.0.6 / sleep_finish v1.0.4）：
1. main() 开头装 SIGTERM 处理器（转 SystemExit(143) → 既有 finally 照常回收）；
2. 启动时兜底清扫 ≥24h 的陈旧孤儿（形状判据 = gettempdir 直接子项 ∧ 本族前缀）；
3. **并行兄弟安全**：新鲜同前缀目录（别的在飞执行）与异前缀目录不得被清扫误删。

对照锚点：同法打到 gpu_finish v1.0.6（修复前版本）⇒ 子进程死于信号 15、
陈旧孤儿原样留着——泄漏路径确由新版本堵住。

e2e 形态：子进程真实加载入口脚本（stub 注入 sys.modules 的假 ``_lib``），
在 pull 阶段阻塞，父进程发 SIGTERM——走的是真信号路径，不是 in-process 模仿。
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = REPO_ROOT / "backend" / "agent" / "scripts"

# (标签, 版本目录, 入口文件, 前缀)
FIXED = [
    ("gpu_finish", "gpu_finish/gpu_finish.py", "gpu-results-"),
    ("powercycle_finish", "powercycle_finish/powercycle_finish.py", "powercycle-results-"),
    ("sleep_finish", "sleep_finish/sleep_finish.py", "sleep-results-"),
]

_DRIVER = '''
import json, os, pathlib, sys, time, types

entry = pathlib.Path(os.environ["FINISHTEST_ENTRY"])
marker = pathlib.Path(os.environ["FINISHTEST_MARKER"])

lib = types.ModuleType("_lib")

def _adb(*args, timeout=60):
    if args and str(args[0]) == "pull":
        time.sleep(120)          # 模拟 adb pull 挂满墙钟，等父进程 SIGTERM
    return (0, "", "")

lib.adb = _adb
lib.adb_shell = lambda *a, **k: "1"
lib.device_online = lambda: True
lib.device_serial = lambda: "TESTSERIAL"
lib.output_result = lambda success, **kw: print(
    json.dumps({"success": success, **kw}, ensure_ascii=False))
lib.params = lambda: {}
lib.param_or_env = lambda cfg, key, env_key, default: cfg.get(key, default)
lib.parse_gpu_log = lambda s: {"test_id": "", "rounds_done": 0, "expected_rounds": 0,
                               "failed_rounds": 0, "end_rc": 0, "junit_failed_rounds": 0,
                               "rounds": []}
lib.parse_sleep_result = lambda b: {"final_status": "PASS", "cycles_done": 0,
                                    "expected_cycles": 0, "wake_failures": 0,
                                    "sleep_anomalies": 0, "entries": []}
lib.parse_powercycle_result = lambda b: {"final_status": "PASS", "cycles_done": 0,
                                         "expected_cycles": 0, "reboot_failures": 0,
                                         "entries": []}
lib.parse_size_from_ls = lambda s: 10
lib.result_log_bytes = lambda: 1
lib.result_paths = lambda: ["/sdcard/Android/data/x/files/STP/result.txt"]
lib.results_dir = lambda project: pathlib.Path(os.environ["FINISHTEST_TMP"]) / "nfs"
lib.stop_stress = lambda *a, **k: None
lib.stop_task = lambda *a, **k: None
lib._DEVICE_SCRIPT = "/sdcard/Auto/gpu_stress_loop.sh"
lib._RESULT_LOG = "/sdcard/Auto/test_log.txt"
sys.modules["_lib"] = lib

import importlib.util
spec = importlib.util.spec_from_file_location("entry", entry)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# 本次自建的目录一登记就通知父进程（此刻起信号路径必然有可回收对象）
_orig_mk = mod._mk_result_tmpdir
def _mk_and_signal():
    p = _orig_mk()
    marker.touch()
    return p
mod._mk_result_tmpdir = _mk_and_signal

mod.main()
'''


@pytest.fixture()
def seeded_tmp(tmp_path, monkeypatch):
    """子进程隔离的 TMPDIR：预置 48h 陈旧孤儿 + 新鲜兄弟 + 异前缀目录。"""
    work = tmp_path / "tmpdir"
    work.mkdir()
    stale = work / "seed-stale"
    stale.mkdir()
    old = time.time() - 48 * 3600
    os.utime(stale, (old, old))
    return work


def _spawn(entry: Path, work: Path) -> subprocess.Popen:
    env = {
        **os.environ,
        "TMPDIR": str(work),
        "FINISHTEST_ENTRY": str(entry),
        "FINISHTEST_MARKER": str(work / "marker"),
        "FINISHTEST_TMP": str(work.parent),
        "STP_DEVICE_SERIAL": "TESTSERIAL",
        "STP_STEP_PARAMS": "{}",
    }
    drv = work.parent / "driver.py"
    drv.write_text(_DRIVER, encoding="utf-8")
    return subprocess.Popen(
        [sys.executable, str(drv)], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def _wait_marker(work: Path, timeout: float = 30.0) -> None:
    marker = work / "marker"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if marker.exists():
            return
        time.sleep(0.1)
    raise AssertionError("子进程未走到 _mk_result_tmpdir（driver 前置形态变了？）")


@pytest.mark.parametrize("family,rel,prefix", FIXED, ids=[f[0] for f in FIXED])
def test_sigterm_reclaims_dirs_and_sweep_keeps_siblings(family, rel, prefix, tmp_path, seeded_tmp):
    work = seeded_tmp
    stale = work / f"{prefix}orphan-48h"
    stale.mkdir()
    old = time.time() - 48 * 3600
    os.utime(stale, (old, old))
    sib = work / f"{prefix}sibling-fresh"
    sib.mkdir()
    other = work / "other-fresh"
    other.mkdir()

    proc = _spawn(_SCRIPTS / rel, work)
    try:
        _wait_marker(work)
        proc.send_signal(signal.SIGTERM)
        rc = proc.wait(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    assert rc == 143, (
        f"{family}: 退出码 {rc}——SIGTERM 未走 SystemExit→finally"
        f"（-15=无处理器被信号打死，0/1=提前退出没有信号路径可断）"
    )
    assert not stale.exists(), f"{family}: ≥24h 陈旧孤儿未被启动清扫（#2980 兜底未生效）"
    assert sib.exists(), f"{family}: 清扫误删并行兄弟的活跃目录（形状/陈旧判据失守）"
    assert other.exists(), f"{family}: 清扫越出了本族前缀"
    left = [p.name for p in work.iterdir() if p.name.startswith(prefix) and p.name != "marker"]
    assert left == [sib.name], f"{family}: SIGTERM 后本次临时目录未回收，残留 {left}"


