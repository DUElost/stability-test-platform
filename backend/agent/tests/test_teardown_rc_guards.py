# -*- coding: utf-8 -*-
"""teardown 系 rc 守门回归（#807）——monkey_teardown / stop_aimonkey v1.0.1。

覆盖：设备离线预检、pull/kill/force-stop rc 失败、post-kill ps 不可验证、
optional pull 项跳过。加载方式同 test_base_tools_rc_guards.py。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from subprocess import CompletedProcess

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


class _Output:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def __call__(self, success, **kwargs) -> None:
        self.payloads.append({"success": success, **kwargs})

    @property
    def last(self) -> dict:
        assert self.payloads, "output_result was not called"
        return self.payloads[-1]


def _cp(returncode: int = 0, stdout: str = "", stderr: str = "") -> CompletedProcess:
    return CompletedProcess(args=["adb"], returncode=returncode, stdout=stdout, stderr=stderr)


# ── monkey_teardown v1.0.1 ───────────────────────────────────────────────────


def _prep_mt(monkeypatch, mod, tmp_path, *, ready_rc=0, pull_rcs=None):
    capture = _Output()
    monkeypatch.setattr(mod, "device_serial", lambda: "SERIAL")
    monkeypatch.setattr(mod, "params", lambda: {})
    monkeypatch.setattr(mod, "output_result", capture)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(mod, "adb_shell_quiet", lambda cmd, timeout=10: _cp(ready_rc))
    monkeypatch.setattr(
        mod, "_kill_processes", lambda serial, names: {"killed": [], "errors": []}
    )
    rcs = dict(pull_rcs or {})

    def fake_run_adb(serial, args, timeout=60):
        return rcs.get(args[1], 0)

    monkeypatch.setattr(mod, "_run_adb", fake_run_adb)
    monkeypatch.setenv("STP_LOG_DIR", str(tmp_path))
    return capture


def test_monkey_teardown_unreachable_device_fails_precheck(monkeypatch, tmp_path):
    mod = _load("monkey_teardown_v101_precheck", "monkey_teardown/v1.0.1/monkey_teardown.py")
    capture = _prep_mt(monkeypatch, mod, tmp_path, ready_rc=1)

    mod.main()

    assert capture.last["success"] is False
    assert "not reachable" in capture.last["error_message"]


def test_monkey_teardown_pull_failure_reports_error_and_skips_optional(monkeypatch, tmp_path):
    mod = _load("monkey_teardown_v101_pull", "monkey_teardown/v1.0.1/monkey_teardown.py")
    capture = _prep_mt(
        monkeypatch,
        mod,
        tmp_path,
        pull_rcs={"/sdcard/Monkeylog.txt": 1, "/sdcard/systeminfo": 1, "/sdcard/Auto": 1},
    )

    mod.main()

    assert capture.last["success"] is False
    assert "pull failed" in capture.last["error_message"]
    # Auto 为 optional：rc=1 记 skipped 不计失败；仅 1 条无 error 的记录
    assert capture.last["metrics"]["pulled_count"] == 1


def test_monkey_teardown_success_counts_pulled(monkeypatch, tmp_path):
    mod = _load("monkey_teardown_v101_ok", "monkey_teardown/v1.0.1/monkey_teardown.py")
    capture = _prep_mt(monkeypatch, mod, tmp_path)

    mod.main()

    assert capture.last["success"] is True
    assert capture.last["metrics"]["pulled_count"] == 3


def test_monkey_teardown_ps_failure_recorded(monkeypatch):
    mod = _load("monkey_teardown_v101_ps", "monkey_teardown/v1.0.1/monkey_teardown.py")
    monkeypatch.setattr(mod, "adb_shell_quiet", lambda cmd, timeout=10: _cp(0))
    monkeypatch.setattr(
        mod.subprocess, "run", lambda *a, **k: _cp(1, "", "device offline")
    )

    result = mod._kill_processes("SERIAL", ["com.android.commands.monkey"])

    assert any("ps -ef failed" in e for e in result["errors"])
    assert result["killed"] == []


# ── stop_aimonkey v1.0.1 ─────────────────────────────────────────────────────


def _quiet_responder(*, ready=0, kill=0, stop=0, extra=0, test_f_missing=True):
    def f(cmd, timeout=10):
        if cmd == "echo ready":
            return _cp(ready)
        if cmd.startswith("kill -9"):
            return _cp(kill)
        if cmd.startswith("am force-stop"):
            return _cp(stop)
        if cmd.startswith("test -f"):
            return _cp(0, "" if test_f_missing else "EXISTS\n")
        return _cp(extra)

    return f


def _prep_sa(monkeypatch, mod, ps_sequence, responder):
    capture = _Output()
    monkeypatch.setattr(mod, "device_serial", lambda: "SERIAL")
    monkeypatch.setattr(mod, "params", lambda: {})
    monkeypatch.setattr(mod, "output_result", capture)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(mod, "adb_shell_quiet", responder)
    state = {"n": 0}

    def fake_ps(serial, patterns, timeout=10):
        i = state["n"]
        state["n"] += 1
        return ps_sequence[min(i, len(ps_sequence) - 1)]

    monkeypatch.setattr(mod, "_ps_grep", fake_ps)
    return capture


def _match(pid: str) -> dict:
    return {"pid": pid, "name": "com.android.commands.monkey", "line": f"u 0 {pid} monkey"}


def test_stop_aimonkey_unreachable_device_fails_precheck(monkeypatch):
    mod = _load("stop_aimonkey_v101_precheck", "stop_aimonkey/v1.0.1/stop_aimonkey.py")
    capture = _prep_sa(
        monkeypatch, mod, [(0, [])], _quiet_responder(ready=1)
    )

    mod.main()

    assert capture.last["success"] is False
    assert "not reachable" in capture.last["error_message"]


def test_stop_aimonkey_kill_failure_reports_error(monkeypatch):
    mod = _load("stop_aimonkey_v101_kill", "stop_aimonkey/v1.0.1/stop_aimonkey.py")
    capture = _prep_sa(
        monkeypatch,
        mod,
        [(0, [_match("111")]), (0, [])],
        _quiet_responder(kill=1, stop=1),
    )

    mod.main()

    assert capture.last["success"] is False
    assert "kill -9 111: rc=1" in capture.last["error_message"]


def test_stop_aimonkey_post_ps_failure_is_not_clear(monkeypatch):
    mod = _load("stop_aimonkey_v101_postps", "stop_aimonkey/v1.0.1/stop_aimonkey.py")
    capture = _prep_sa(
        monkeypatch,
        mod,
        [(0, [_match("111")]), (1, [])],  # post-kill ps 不可验证
        _quiet_responder(),
    )

    mod.main()

    assert capture.last["success"] is False
    assert "cannot verify" in capture.last["error_message"]
    assert capture.last["metrics"]["monkey_processes_remaining"] == -1


def test_stop_aimonkey_success(monkeypatch):
    mod = _load("stop_aimonkey_v101_ok", "stop_aimonkey/v1.0.1/stop_aimonkey.py")
    capture = _prep_sa(
        monkeypatch, mod, [(0, [_match("111")]), (0, [])], _quiet_responder()
    )

    mod.main()

    assert capture.last["success"] is True
    assert capture.last["metrics"]["killed_pids"] == ["111"]
    assert capture.last["metrics"]["monkey_processes_remaining"] == 0
