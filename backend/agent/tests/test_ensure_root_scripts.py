# -*- coding: utf-8 -*-
"""ensure_root 脚本侧测试（#2802 配套：v1.0.1 失败证据字段）。

加载方式对齐 test_check_device_scripts.py：importlib + sys.path 注入（版本目录
自带 `_adb.py`），加载前后清 `sys.modules['_adb']` 避免串库。

覆盖：已是 root → skip 语义不变 / adb root 成功路径不变 / 失败报文带
adb_root rc+stdout+stderr + id_u + adb_state / 异常路径 / 截断有界 / max_attempts 生效。
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
def _env(monkeypatch):
    monkeypatch.setenv("STP_DEVICE_SERIAL", "SERIAL-A")
    monkeypatch.delenv("STP_STEP_PARAMS", raising=False)
    # 真实 sleep（retry_delay=3s）在测试里全部替换掉
    monkeypatch.setattr(sys.modules[__name__], "_noop_sleep", lambda *_: None, raising=False)


class _Completed:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _patch(monkeypatch, mod, *, id_u=("2000\n",), root=("restarting adbd as root\n",), state="offline\n"):
    """按子命令分派假 subprocess；id_u/root 可给列表按调用顺序消费。"""
    calls: list[list[str]] = []
    idu_iter = list(id_u) if isinstance(id_u, (list, tuple)) else [id_u]
    root_iter = list(root) if isinstance(root, (list, tuple)) else [root]

    def fake_run(cmd, capture_output=True, text=True, timeout=10):
        calls.append(list(cmd))
        last = cmd[-1] if cmd else ""
        if last == "id -u":
            item = idu_iter[min(len([c for c in calls if c[-1] == "id -u"]) - 1, len(idu_iter) - 1)]
            if isinstance(item, BaseException):
                raise item
            return item if isinstance(item, _Completed) else _Completed(stdout=item)
        if last == "root":
            item = root_iter[min(len([c for c in calls if c[-1] == "root"]) - 1, len(root_iter) - 1)]
            if isinstance(item, BaseException):
                raise item
            return item if isinstance(item, _Completed) else _Completed(stdout=item)
        if last == "get-state":
            if isinstance(state, BaseException):
                raise state
            return _Completed(stdout=state)
        raise AssertionError(f"unexpected adb call: {cmd}")

    monkeypatch.setattr(mod, "subprocess", types.SimpleNamespace(run=fake_run, TimeoutExpired=subprocess.TimeoutExpired))
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    return calls


def _result(capsys) -> dict:
    out = capsys.readouterr().out.strip().splitlines()
    assert out, "脚本未输出 JSON"
    return json.loads(out[-1])
















# ── v1.0.2（#2802 D1″）：重启撞峰吸收（boot 门 + 有界重试） ──────────────────


@pytest.fixture(scope="module")
def er_mod_v102():
    return _load("ensure_root_v102", "ensure_root/ensure_root.py")


def _patch_v102(monkeypatch, mod, *, id_u=None, root=None, states=None, boot=None):
    """v1.0.2 假 adb：id -u / root / get-state / getprop，按调用顺序消费列表。"""
    calls: list[list[str]] = []
    idu_iter = list(id_u) if isinstance(id_u, (list, tuple)) else [id_u if id_u is not None else "2000\n"]
    root_iter = list(root) if isinstance(root, (list, tuple)) else [root if root is not None else ("\n",)]
    state_iter = list(states) if isinstance(states, (list, tuple)) else [states if states is not None else _Completed(stdout="device\n")]
    boot_iter = list(boot) if isinstance(boot, (list, tuple)) else [boot if boot is not None else _Completed(stdout="1\n")]

    def take(seq, n):
        return seq[min(n - 1, len(seq) - 1)]

    def fake_run(cmd, capture_output=True, text=True, timeout=10):
        calls.append(list(cmd))
        last = cmd[-1] if cmd else ""
        if last == "id -u":
            item = take(idu_iter, sum(1 for c in calls if c[-1] == "id -u"))
            if isinstance(item, BaseException):
                raise item
            return item if isinstance(item, _Completed) else _Completed(stdout=item)
        if last == "root":
            item = take(root_iter, sum(1 for c in calls if c[-1] == "root"))
            if isinstance(item, BaseException):
                raise item
            return item if isinstance(item, _Completed) else _Completed(stdout=item)
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
        raise AssertionError(f"unexpected adb call: {cmd}")

    monkeypatch.setattr(
        mod, "subprocess",
        types.SimpleNamespace(run=fake_run, TimeoutExpired=subprocess.TimeoutExpired),
    )
    # 只把等待压缩成 0，不动 monotonic（预算判定保持真实）
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    return calls


def test_v102_already_root_skips_unchanged(er_mod_v102, monkeypatch, capsys):
    """skip 语义不变：已是 root 时不调 adb root、不触发就绪轮询。"""
    calls = _patch_v102(monkeypatch, er_mod_v102, id_u="0\n")

    er_mod_v102.main()

    payload = _result(capsys)
    assert payload["success"] is True and payload["skipped"] is True
    assert calls == [["adb", "-s", "SERIAL-A", "shell", "id -u"]]


def test_v102_fast_path_first_attempt(er_mod_v102, monkeypatch, capsys):
    """正常设备：首次 adb root 即过，不触发就绪轮询。"""
    calls = _patch_v102(monkeypatch, er_mod_v102, id_u=["2000\n", "0\n"])

    er_mod_v102.main()

    payload = _result(capsys)
    assert payload["success"] is True
    assert payload["metrics"]["attempts"] == 1
    assert payload["metrics"]["waited_ready"] is False
    assert not [c for c in calls if c[-1] == "get-state"]


def test_v102_reboot_window_absorbed_by_retry(er_mod_v102, monkeypatch, capsys):
    """第一趟 not found（设备在重启窗口）→ 等就绪 → 第二趟通过（r477 的 48 条形状）。"""
    _patch_v102(
        monkeypatch, er_mod_v102,
        id_u=["2000\n", "2000\n", "0\n"],
        root=[_Completed(stdout="", stderr="adb: unable to connect for root: device 'S-A' not found\n", returncode=1),
              _Completed(stdout="restarting adbd as root\n")],
        states=[_Completed(stdout="device\n")],
        boot=[_Completed(stdout="1\n")],
    )

    er_mod_v102.main()

    payload = _result(capsys)
    assert payload["success"] is True
    assert payload["metrics"]["attempts"] == 2
    assert payload["metrics"]["waited_ready"] is True


def test_v102_waits_until_boot_completed(er_mod_v102, monkeypatch, capsys):
    """get-state=device 但 boot_completed 尚未 1 时要继续等（同 check_device v1.0.2 判定）。"""
    calls = _patch_v102(
        monkeypatch, er_mod_v102,
        id_u=["2000\n", "2000\n", "0\n"],
        root=[_Completed(stdout="", stderr="device 'S-A' not found\n", returncode=1),
              _Completed(stdout="restarting adbd as root\n")],
        states=[_Completed(stdout="device\n")],
        boot=[_Completed(stdout="0\n"), _Completed(stdout="1\n")],
    )

    er_mod_v102.main()

    assert _result(capsys)["metrics"]["attempts"] == 2
    # 第一轮 boot_completed=0 必须继续轮询（而非放行）——去掉 boot 门会让这里退化成 1 次
    assert sum(1 for c in calls if c[-1].endswith("sys.boot_completed")) >= 2


def test_v102_no_root_history_tag_and_evidence(er_mod_v102, monkeypatch, capsys):
    """adb root rc=0 但 id -u != 0（userbuild 拒绝）：history 标 no_root，证据字段不丢。"""
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps(
        {"max_attempts": 2, "wait_ready_seconds": 1, "total_budget_seconds": 3, "retry_delay_seconds": 0}))
    _patch_v102(
        monkeypatch, er_mod_v102,
        id_u=["2000\n", "2000\n", "2000\n"],
        root=_Completed(stdout="restarting adbd as root\n", returncode=0),
        states=[_Completed(stdout="device\n")],
    )

    with pytest.raises(SystemExit) as exc:
        er_mod_v102.main()
    assert exc.value.code == 1

    msg = _result(capsys)["error_message"]
    assert "Root access not granted after 2 attempts" in msg  # 兼容既有 grep/告警口径
    assert "attempts=2/2" in msg and "history=[1:no_root,2:no_root]" in msg
    assert "adb_root rc=0" in msg and "id_u='2000'" in msg and "adb_state='device' rc=0" in msg


def test_v102_never_ready_breaks_early(er_mod_v102, monkeypatch, capsys):
    """设备一直不在（重启循环）：等不到就绪即收手，history 标 not_ready（不再空转）。"""
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps(
        {"max_attempts": 3, "wait_ready_seconds": 1, "total_budget_seconds": 2, "retry_delay_seconds": 0}))
    calls = _patch_v102(
        monkeypatch, er_mod_v102,
        id_u="2000\n",
        root=_Completed(stdout="", stderr="adb: device 'S-A' not found\n", returncode=1),
        states=[_Completed(stdout="", stderr="error: device 'S-A' not found\n", returncode=1)],
    )

    with pytest.raises(SystemExit):
        er_mod_v102.main()

    msg = _result(capsys)["error_message"]
    assert "not_ready" in msg
    assert len([c for c in calls if c[-1] == "root"]) == 1  # 第二趟没就绪就不再打 root


def test_v102_message_bounded(er_mod_v102, monkeypatch, capsys):
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps(
        {"max_attempts": 2, "wait_ready_seconds": 1, "total_budget_seconds": 2, "retry_delay_seconds": 0}))
    _patch_v102(
        monkeypatch, er_mod_v102,
        id_u="2000\n",
        root=_Completed(stdout="x" * 5000, stderr="y" * 5000, returncode=9),
        states=[_Completed(stdout="device\n")],
    )

    with pytest.raises(SystemExit):
        er_mod_v102.main()

    msg = _result(capsys)["error_message"]
    assert "…" in msg and len(msg) < 800


def test_v102_get_state_failure_does_not_break_judgement(er_mod_v102, monkeypatch, capsys):
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps(
        {"max_attempts": 2, "wait_ready_seconds": 1, "total_budget_seconds": 2, "retry_delay_seconds": 0}))
    _patch_v102(
        monkeypatch, er_mod_v102,
        id_u="2000\n",
        root=_Completed(stdout="", stderr="err\n", returncode=1),
        states=RuntimeError("adb server died"),
    )

    with pytest.raises(SystemExit):
        er_mod_v102.main()

    assert "adb_state=<RuntimeError>" in _result(capsys)["error_message"]
