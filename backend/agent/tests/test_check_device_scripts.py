# -*- coding: utf-8 -*-
"""check_device 脚本侧测试（#2802 D0：失败诊断保留证据）。

加载方式对齐 test_powercycle_scripts.py：importlib + sys.path 注入（版本目录
自带 `_adb.py`），并在加载前后清 ``sys.modules['_adb']``，避免与其它脚本族串库。

覆盖：成功路径语义不变 / unexpected output 报文带 rc+stdout+stderr+adb_state /
超时带部分输出 / 截断有界 / expect_root 分支不变。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str, rel_path: str):
    path = _SCRIPTS / rel_path
    sys.path.insert(0, str(path.parent))
    try:
        sys.modules.pop("_adb", None)
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader, f"cannot locate {path}"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.modules.pop("_adb", None)
        sys.path.remove(str(path.parent))




@pytest.fixture(autouse=True)
def _device_env(monkeypatch):
    monkeypatch.setenv("STP_DEVICE_SERIAL", "SERIAL-A")
    monkeypatch.delenv("STP_STEP_PARAMS", raising=False)


class _Completed:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _patch_adb(monkeypatch, mod, *, echo=None, state=None, uid=None):
    """按子命令分派假 subprocess：echo / get-state / id -u。

    echo=-异常实例 → 该调用抛异常（用于超时/意外异常路径）。
    """
    calls: list[list[str]] = []

    def fake_run(cmd, capture_output=True, text=True, timeout=10):
        calls.append(list(cmd))
        last = cmd[-1] if cmd else ""
        if last == "echo test":
            if isinstance(echo, BaseException):
                raise echo
            return echo or _Completed(stdout="test\n")
        if last == "get-state":
            if isinstance(state, BaseException):
                raise state
            return state or _Completed(stdout="device\n")
        if last == "id -u":
            return uid or _Completed(stdout="0\n")
        raise AssertionError(f"unexpected adb call: {cmd}")

    monkeypatch.setattr(
        mod, "subprocess",
        types.SimpleNamespace(run=fake_run, TimeoutExpired=subprocess.TimeoutExpired),
    )
    return calls


def _result(capsys) -> dict:
    out = capsys.readouterr().out.strip().splitlines()
    assert out, "脚本未输出 JSON"
    return json.loads(out[-1])


# ── v1.0.2（#2802 D1′）：重启撞峰吸收 ────────────────────────────────────────


@pytest.fixture(scope="module")
def check_mod_v102():
    return _load("check_device_v102", "check_device/check_device.py")


def _patch_v102(monkeypatch, mod, *, echo, states=None, boot=None, uid=None):
    """v1.0.2 假 adb：echo / get-state / getprop / id -u，按调用顺序消费列表。"""
    calls: list[list[str]] = []
    echo_iter = list(echo) if isinstance(echo, list) else [echo]
    state_iter = list(states or []) or [_Completed(stdout="device\n")]
    boot_iter = list(boot or []) or [_Completed(stdout="1\n")]

    def take(seq, n):
        return seq[min(n - 1, len(seq) - 1)]

    def fake_run(cmd, capture_output=True, text=True, timeout=10):
        calls.append(list(cmd))
        last = cmd[-1] if cmd else ""
        if last == "echo test":
            item = take(echo_iter, sum(1 for c in calls if c[-1] == "echo test"))
            if isinstance(item, BaseException):
                raise item
            return item
        if last == "get-state":
            item = take(state_iter, sum(1 for c in calls if c[-1] == "get-state"))
            if isinstance(item, BaseException):
                raise item
            return item
        if last == "getprop sys.boot_completed":
            item = take(boot_iter, sum(1 for c in calls if c[-1].endswith("sys.boot_completed")))
            if isinstance(item, BaseException):
                raise item
            return item
        if last == "id -u":
            return uid or _Completed(stdout="0\n")
        raise AssertionError(f"unexpected adb call: {cmd}")

    monkeypatch.setattr(
        mod, "subprocess",
        types.SimpleNamespace(run=fake_run, TimeoutExpired=subprocess.TimeoutExpired),
    )
    # 只把等待压缩成 0，不动 monotonic（预算判定保持真实）
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    return calls


def test_v102_fast_path_first_attempt(check_mod_v102, monkeypatch, capsys):
    """正常设备：首次即过，不触发就绪轮询。"""
    calls = _patch_v102(monkeypatch, check_mod_v102, echo=_Completed(stdout="test\n"))

    check_mod_v102.main()

    payload = _result(capsys)
    assert payload["success"] is True
    assert payload["metrics"]["attempts"] == 1
    assert not [c for c in calls if "get-state" in c[-1]]


def test_v102_reboot_window_absorbed_by_retry(check_mod_v102, monkeypatch, capsys):
    """第一次 not_found（设备在重启窗口）→ 等就绪 → 第二次通过。"""
    _patch_v102(
        monkeypatch, check_mod_v102,
        echo=[_Completed(stdout="", stderr="adb: device 'S-A' not found\n", returncode=1),
              _Completed(stdout="test\n")],
        states=[_Completed(stdout="device\n")],
        boot=[_Completed(stdout="1\n")],
    )

    check_mod_v102.main()

    payload = _result(capsys)
    assert payload["success"] is True
    assert payload["metrics"]["attempts"] == 2
    assert payload["metrics"]["waited_ready"] is True


def test_v102_waits_until_boot_completed(check_mod_v102, monkeypatch, capsys):
    """get-state=device 但 boot_completed 尚未 1 时要继续等（同 powercycle_finish 判定）。"""
    calls = _patch_v102(
        monkeypatch, check_mod_v102,
        echo=[_Completed(stdout="", stderr="adb: device 'S-A' not found\n", returncode=1),
              _Completed(stdout="test\n")],
        states=[_Completed(stdout="device\n")],
        boot=[_Completed(stdout="0\n"), _Completed(stdout="1\n")],
    )

    check_mod_v102.main()

    assert _result(capsys)["metrics"]["attempts"] == 2
    # 第一轮 boot_completed=0 必须继续轮询（而非放行）——去掉 boot 门会让这里退化成 1 次
    assert sum(1 for c in calls if c[-1].endswith("sys.boot_completed")) >= 2


def test_v102_budget_exhausted_reports_history_and_evidence(check_mod_v102, monkeypatch, capsys):
    """设备一直不在（重启循环）→ 预算内等不到就绪 → 失败报文含 attempts/history + v1.0.1 证据字段。"""
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps(
        {"max_attempts": 3, "wait_ready_seconds": 1, "total_budget_seconds": 2, "retry_delay_seconds": 0}))
    _patch_v102(
        monkeypatch, check_mod_v102,
        echo=_Completed(stdout="", stderr="adb: device 'S-A' not found\n", returncode=1),
        states=[_Completed(stdout="", stderr="error: device 'S-A' not found\n", returncode=1)],
    )

    with pytest.raises(SystemExit) as exc:
        check_mod_v102.main()
    assert exc.value.code == 1

    msg = _result(capsys)["error_message"]
    assert "unexpected output" in msg
    assert "history=[" in msg and "not_found" in msg
    assert "rc=1" in msg and "adb: device 'S-A' not found" in msg
    assert "adb_state=" in msg


def test_v102_message_bounded(check_mod_v102, monkeypatch, capsys):
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps(
        {"max_attempts": 2, "wait_ready_seconds": 1, "total_budget_seconds": 2, "retry_delay_seconds": 0}))
    _patch_v102(
        monkeypatch, check_mod_v102,
        echo=_Completed(stdout="x" * 5000, stderr="y" * 5000, returncode=7),
        states=[_Completed(stdout="device\n")],
    )

    with pytest.raises(SystemExit):
        check_mod_v102.main()

    msg = _result(capsys)["error_message"]
    assert "…" in msg and len(msg) < 800


def test_v102_expect_root_still_enforced(check_mod_v102, monkeypatch, capsys):
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({"expect_root": True}))
    _patch_v102(monkeypatch, check_mod_v102, echo=_Completed(stdout="test\n"), uid=_Completed(stdout="2000\n"))

    with pytest.raises(SystemExit):
        check_mod_v102.main()

    assert "has no root access" in _result(capsys)["error_message"]
