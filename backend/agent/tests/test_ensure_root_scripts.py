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
_VERSION_DIR = "ensure_root/v1.0.1"


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


@pytest.fixture(scope="module")
def er_mod():
    return _load("ensure_root_v101", f"{_VERSION_DIR}/ensure_root.py")


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


def test_already_root_skips(er_mod, monkeypatch, capsys):
    calls = _patch(monkeypatch, er_mod, id_u="0\n")

    er_mod.main()

    payload = _result(capsys)
    assert payload["success"] is True and payload["skipped"] is True
    assert calls == [["adb", "-s", "SERIAL-A", "shell", "id -u"]]  # 不该调 adb root


def test_root_granted_after_adb_root(er_mod, monkeypatch, capsys):
    _patch(monkeypatch, er_mod, id_u=["2000\n", "0\n"])

    er_mod.main()

    payload = _result(capsys)
    assert payload["success"] is True
    assert payload["metrics"]["attempts"] == 1


def test_failure_carries_rc_stdout_stderr_id_u_and_state(er_mod, monkeypatch, capsys):
    """v1.0.0 只写 'Root access not granted…'；本版必须带四类证据（#2802 配套）。"""
    _patch(
        monkeypatch, er_mod,
        id_u=["2000\n", "2000\n", "2000\n", "2000\n"],
        root=_Completed(stdout="", stderr="adbd cannot run as root in production builds\n", returncode=1),
        state="device\n",
    )

    with pytest.raises(SystemExit) as exc:
        er_mod.main()
    assert exc.value.code == 1

    msg = _result(capsys)["error_message"]
    assert "Root access not granted after 3 attempts" in msg  # 兼容既有 grep/告警口径
    assert "adb_root rc=1" in msg
    assert "adbd cannot run as root in production builds" in msg
    assert "id_u='2000'" in msg
    assert "adb_state='device' rc=0" in msg


def test_failure_with_exception_reports_exc(er_mod, monkeypatch, capsys):
    _patch(monkeypatch, er_mod, id_u="2000\n", root=RuntimeError("adb server died"), state="offline\n")

    with pytest.raises(SystemExit):
        er_mod.main()

    msg = _result(capsys)["error_message"]
    assert "adb root failed after 3 attempts" in msg
    assert "RuntimeError: adb server died" in msg
    assert "adb_state='offline'" in msg


def test_long_output_is_bounded(er_mod, monkeypatch, capsys):
    _patch(
        monkeypatch, er_mod,
        id_u="2000\n",
        root=_Completed(stdout="x" * 5000, stderr="y" * 5000, returncode=9),
    )

    with pytest.raises(SystemExit):
        er_mod.main()

    msg = _result(capsys)["error_message"]
    assert "…" in msg
    assert len(msg) < 800, f"报文未截断：{len(msg)} 字符"


def test_max_attempts_param_respected(er_mod, monkeypatch, capsys):
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({"max_attempts": 1}))
    calls = _patch(monkeypatch, er_mod, id_u="2000\n", root=_Completed(stdout="", stderr="err", returncode=1))

    with pytest.raises(SystemExit):
        er_mod.main()

    assert len([c for c in calls if c[-1] == "root"]) == 1
    assert "after 1 attempts" in _result(capsys)["error_message"]


def test_get_state_failure_does_not_break_judgement(er_mod, monkeypatch, capsys):
    _patch(monkeypatch, er_mod, id_u="2000\n", root=_Completed(stdout="", stderr="err", returncode=1), state=RuntimeError("x"))

    with pytest.raises(SystemExit):
        er_mod.main()

    assert "adb_state=<RuntimeError>" in _result(capsys)["error_message"]
