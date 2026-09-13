# -*- coding: utf-8 -*-
"""#809 monkey 看门狗链三缺口的行为测试。

1. aimwd 是普通脚本文件（bundle 实测 241B ASCII）——旧 is_dir 守卫让它永远
   推不上设备，双看门狗只剩单层。修后：按 is_file 推送 + 设备侧回验 + 启动后
   ps 确认 MonkeyWatchdog；失败计入 errors / fail。
2. monkey_check v2.0.2 重启分支假成功——nohup 异步启动的 shell rc 恒 0。
   修后：在 ≤60s 窗口内轮询 ps 见到 MonkeyTest.sh 与 MonkeyWatchdog 才算
   重启成功，超时 False。
3. monkey_test 推送失败不记 error、monkey_running 不作成功门禁。修后：
   黑名单/看门狗/媒体推送 rc 计入 errors；monkey 进程在 ≥15s 窗口复查，
   仍未见即失败。

版本目录不可变：本文件只加载新版本（monkey_test v1.2.2 / monkey_check
v2.0.3 / monkey_launch v5.0.2 / monkey_resource_push v1.0.1）。
"""

from __future__ import annotations

import importlib.util
import sys
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


def _common_patches(monkeypatch, mod, *, ps_map, pushes, fail_push=()):
    """device_serial/params/output_result/_shell/_push_file 的统一替身。"""
    captured: dict = {}
    shell_calls: list[str] = []

    monkeypatch.setattr(mod, "device_serial", lambda: "SERIAL")
    monkeypatch.setattr(mod, "params", lambda: {"aimonkey_dir": str(ps_map["bundle"])})
    monkeypatch.setattr(
        mod, "_resolve_aimonkey_dir", lambda cfg: Path(cfg["aimonkey_dir"])
    )

    def fake_output_result(success, error_message=None, metrics=None):
        captured.update(
            {"success": success, "error_message": error_message, "metrics": metrics or {}}
        )

    monkeypatch.setattr(mod, "output_result", fake_output_result)

    def fake_shell(serial, cmd, timeout=30):
        shell_calls.append(cmd)
        if "test -s /data/local/tmp/aimwd" in cmd:
            return (0, ps_map.get("verify_aimwd", "OK"))
        return (0, "")

    monkeypatch.setattr(mod, "_shell", fake_shell)

    def fake_push(serial, local, remote, timeout=60):
        if remote in fail_push or Path(local).name in fail_push:
            return False
        pushes.append(remote)
        return True

    monkeypatch.setattr(mod, "_push_file", fake_push)
    # 单测不应真睡（轮询 helper 已被桩替，窗口等待只在 helper 单测里用真时钟）
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    return captured, shell_calls


def _bundle(tmp_path: Path, *, aimwd: str = "file", resource_files=()):
    bundle = tmp_path / "AIMonkeyTest"
    bundle.mkdir()
    for f in ("aim", "aim.jar", "aimonkey.apk", "MonkeyTestAi.sh", "blacklist.txt"):
        (bundle / f).write_text("x", encoding="utf-8")
    if aimwd == "file":
        (bundle / "aimwd").write_text("#!/bin/sh\nexec app_process MonkeyWatchdog\n", encoding="utf-8")
    elif aimwd == "dir":
        (bundle / "aimwd").mkdir()
    if resource_files:
        rdir = bundle / "resource"
        rdir.mkdir()
        for f in resource_files:
            (rdir / f).write_text("m", encoding="utf-8")
    return bundle


# ─────────────────────────── monkey_test v1.2.2 ───────────────────────────

def _run_monkey_test(monkeypatch, tmp_path, **kwargs):
    mod = _load("monkey_test_v122", "monkey_test/v1.2.2/monkey_test.py")
    bundle = _bundle(
        tmp_path,
        aimwd=kwargs.get("aimwd", "file"),
        resource_files=kwargs.get("resource_files", ()),
    )
    pushes: list[str] = []
    captured, shell_calls = _common_patches(
        monkeypatch,
        mod,
        ps_map={"bundle": bundle, "verify_aimwd": kwargs.get("verify_aimwd", "OK")},
        pushes=pushes,
        fail_push=kwargs.get("fail_push", ()),
    )

    wait_calls: list[tuple[str, str]] = []

    def fake_wait(serial, pattern, timeout_s, interval_s, exclude=""):
        wait_calls.append((pattern, exclude))
        if pattern == "MonkeyWatchdog":
            return kwargs.get("ps_aimwd", True)
        return kwargs.get("ps_monkey", True)

    monkeypatch.setattr(mod, "_wait_ps_process", fake_wait)

    mod.main()
    return captured, pushes, shell_calls, wait_calls


def test_aimwd_pushed_as_file_and_watchdog_verified(monkeypatch, tmp_path):
    out, pushes, shell_calls, wait_calls = _run_monkey_test(monkeypatch, tmp_path)

    assert "/data/local/tmp/aimwd" in pushes
    assert any("test -s /data/local/tmp/aimwd" in cmd for cmd in shell_calls)  # 设备侧回验
    assert ("MonkeyWatchdog", "") in wait_calls  # 启动后确实验证了 aimwd 进程
    assert ("monkey", "MonkeyWatchdog") in wait_calls  # monkey 检测排除看门狗
    assert out["success"] is True
    assert out["metrics"]["monkey_running"] is True


def test_aimwd_missing_in_bundle_fails(monkeypatch, tmp_path):
    out, pushes, _calls, _wait = _run_monkey_test(monkeypatch, tmp_path, aimwd="none")

    assert "/data/local/tmp/aimwd" not in pushes
    assert out["success"] is False
    assert "aimwd bundle file missing" in out["error_message"]


def test_aimwd_is_dir_no_longer_counts(monkeypatch, tmp_path):
    """旧 is_dir 守卫命中的正是'目录才推'——bundle 里是文件，目录反而应报错。"""
    out, _pushes, _calls, _wait = _run_monkey_test(monkeypatch, tmp_path, aimwd="dir")

    assert out["success"] is False
    assert "aimwd bundle file missing" in out["error_message"]


def test_aimwd_push_failure_recorded(monkeypatch, tmp_path):
    out, _pushes, _calls, _wait = _run_monkey_test(
        monkeypatch, tmp_path, fail_push=("aimwd",)
    )

    assert out["success"] is False
    assert "push failed: aimwd" in out["error_message"]


def test_aimwd_device_verification_failure_recorded(monkeypatch, tmp_path):
    out, _pushes, _calls, _wait = _run_monkey_test(
        monkeypatch, tmp_path, verify_aimwd="MISSING"
    )

    assert out["success"] is False
    assert "aimwd verification failed" in out["error_message"]


def test_aimwd_process_not_observed_fails(monkeypatch, tmp_path):
    out, _pushes, _calls, _wait = _run_monkey_test(
        monkeypatch, tmp_path, ps_aimwd=False
    )

    assert out["success"] is False
    assert "MonkeyWatchdog" in out["error_message"]


@pytest.mark.parametrize(
    "fail_push,expected",
    [
        (("blacklist.txt",), "push failed: blacklist.txt"),
        (("MonkeyTestAi.sh",), "push failed: MonkeyTestAi.sh"),
    ],
)
def test_watchdog_and_blacklist_push_failures_recorded(
    monkeypatch, tmp_path, fail_push, expected
):
    out, _pushes, _calls, _wait = _run_monkey_test(monkeypatch, tmp_path, fail_push=fail_push)

    assert out["success"] is False
    assert expected in out["error_message"]


def test_media_push_failure_recorded(monkeypatch, tmp_path):
    out, _pushes, _calls, _wait = _run_monkey_test(
        monkeypatch,
        tmp_path,
        resource_files=("m1.mp4", "m2.mp4"),
        fail_push=("m2.mp4",),
    )

    assert out["success"] is False
    assert "resource push failed: 1 file(s)" in out["error_message"]
    assert out["metrics"]["resource_push"] == "pushed:1/failed:1"


def test_monkey_running_is_success_gate(monkeypatch, tmp_path):
    out, _pushes, _calls, _wait = _run_monkey_test(monkeypatch, tmp_path, ps_monkey=False)

    assert out["success"] is False
    assert "monkey process not observed within 15s" in out["error_message"]
    assert out["metrics"]["monkey_running"] is False


# ──────────────────── monkey_test 轮询 helper（#809 门禁底座） ────────────────────

def test_wait_ps_process_sees_process_after_retries(monkeypatch):
    mod = _load("monkey_test_v122_wait", "monkey_test/v1.2.2/monkey_test.py")
    seq = ["", "", "123 com.android.commands.monkey.Monkey --x"]

    monkeypatch.setattr(mod, "_shell", lambda serial, cmd, timeout=15: (0, seq.pop(0)))
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

    assert mod._wait_ps_process("S", "monkey", 0.2, 0.001, exclude="MonkeyWatchdog") is True


def test_wait_ps_process_times_out_when_absent(monkeypatch):
    mod = _load("monkey_test_v122_wait2", "monkey_test/v1.2.2/monkey_test.py")

    monkeypatch.setattr(mod, "_shell", lambda serial, cmd, timeout=15: (0, ""))
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

    assert mod._wait_ps_process("S", "monkey", 0.02, 0.001) is False


def test_wait_ps_process_command_carries_watchdog_exclusion(monkeypatch):
    mod = _load("monkey_test_v122_wait3", "monkey_test/v1.2.2/monkey_test.py")
    cmds: list[str] = []

    def fake_shell(serial, cmd, timeout=15):
        cmds.append(cmd)
        return (0, "")

    monkeypatch.setattr(mod, "_shell", fake_shell)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

    mod._wait_ps_process("S", "monkey", 0.0, 0.001, exclude="MonkeyWatchdog")
    assert "grep -v 'MonkeyWatchdog'" in cmds[0]


# ─────────────────────────── monkey_check v2.0.3 ───────────────────────────

def _load_check():
    return _load("monkey_check_v203", "monkey_check/v2.0.3/monkey_check.py")


def test_restart_false_on_shell_rc_nonzero(monkeypatch):
    mod = _load_check()
    monkeypatch.setattr(mod, "_shell", lambda serial, cmd, timeout=30: (1, ""))
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

    assert mod._restart_watchdog("S", timeout_s=0.01, interval_s=0.001) is False


def test_restart_false_when_monkeytest_sh_never_seen(monkeypatch):
    mod = _load_check()
    monkeypatch.setattr(mod, "_shell", lambda serial, cmd, timeout=30: (0, ""))
    monkeypatch.setattr(mod, "_ps_grep", lambda serial, pattern, timeout=10, exclude="": [])
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

    assert mod._restart_watchdog("S", timeout_s=0.02, interval_s=0.001) is False


def test_restart_false_when_aimwd_never_seen(monkeypatch):
    """rc==0 且 MonkeyTest.sh 起来了——但 MonkeyWatchdog 没起来仍是假成功。"""
    mod = _load_check()
    monkeypatch.setattr(mod, "_shell", lambda serial, cmd, timeout=30: (0, ""))

    def fake_ps(serial, pattern, timeout=10, exclude=""):
        return [{"pid": "1", "line": "MonkeyTest.sh"}] if pattern == "MonkeyTest.sh" else []

    monkeypatch.setattr(mod, "_ps_grep", fake_ps)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

    assert mod._restart_watchdog("S", timeout_s=0.02, interval_s=0.001) is False


def test_restart_true_only_when_both_watchdogs_seen(monkeypatch):
    mod = _load_check()
    monkeypatch.setattr(mod, "_shell", lambda serial, cmd, timeout=30: (0, ""))
    monkeypatch.setattr(
        mod,
        "_ps_grep",
        lambda serial, pattern, timeout=10, exclude="": [{"pid": "1", "line": "x"}],
    )
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

    assert mod._restart_watchdog("S", timeout_s=0.02, interval_s=0.001) is True


def test_ps_grep_command_carries_watchdog_exclusion(monkeypatch):
    mod = _load_check()
    cmds: list[str] = []
    monkeypatch.setattr(
        mod, "_shell", lambda serial, cmd, timeout=10: (cmds.append(cmd) or (0, ""))
    )

    mod._ps_grep("S", "com.android.commands.monkey", exclude="MonkeyWatchdog")
    assert "grep -v 'MonkeyWatchdog'" in cmds[0]


def _run_check_main(monkeypatch, *, monkey_alive, watchdog_alive, restart_ok=False):
    mod = _load_check()
    captured: dict = {}
    restart_calls: list[int] = []
    ps_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(mod, "device_serial", lambda: "S")
    monkeypatch.setattr(mod, "params", lambda: {})
    monkeypatch.setattr(mod, "_shell", lambda serial, cmd, timeout=30: (0, ""))

    def fake_ps(serial, pattern, timeout=10, exclude=""):
        ps_calls.append((pattern, exclude))
        if pattern == "MonkeyTest.sh":
            return [{"pid": "9", "line": "MonkeyTest.sh"}] if watchdog_alive else []
        return (
            [{"pid": "7", "line": "com.android.commands.monkey.Monkey"}]
            if monkey_alive
            else []
        )

    monkeypatch.setattr(mod, "_ps_grep", fake_ps)
    monkeypatch.setattr(
        mod,
        "_restart_watchdog",
        lambda serial: (restart_calls.append(1) or restart_ok),
    )

    def fake_output_result(success, error_message=None, metrics=None):
        captured.update(
            {"success": success, "error_message": error_message, "metrics": metrics or {}}
        )

    monkeypatch.setattr(mod, "output_result", fake_output_result)

    exited = False
    try:
        mod.main()
    except SystemExit:
        exited = True
    return captured, exited, restart_calls, ps_calls


def test_check_monkey_detection_excludes_watchdog(monkeypatch):
    _out, _exited, _restart, ps_calls = _run_check_main(
        monkeypatch, monkey_alive=True, watchdog_alive=True
    )

    monkey_calls = [c for c in ps_calls if c[0] == "com.android.commands.monkey"]
    assert monkey_calls
    assert all(exclude == "MonkeyWatchdog" for _pattern, exclude in monkey_calls)


def test_check_fails_when_restart_truly_fails(monkeypatch):
    out, exited, restart_calls, _ps = _run_check_main(
        monkeypatch, monkey_alive=False, watchdog_alive=False, restart_ok=False
    )

    assert restart_calls == [1]
    assert exited is True
    assert out["success"] is False
    assert out["metrics"]["restarted"] is False


def test_check_reports_success_after_verified_restart(monkeypatch):
    out, exited, _restart, _ps = _run_check_main(
        monkeypatch, monkey_alive=False, watchdog_alive=False, restart_ok=True
    )

    assert exited is False
    assert out["success"] is True
    assert out["metrics"]["restarted"] is True
    assert out["metrics"]["watchdog_alive"] is True


def test_check_does_not_restart_when_watchdog_alive(monkeypatch):
    out, exited, restart_calls, _ps = _run_check_main(
        monkeypatch, monkey_alive=False, watchdog_alive=True
    )

    assert restart_calls == []
    assert exited is False
    assert out["success"] is True
    assert out["metrics"]["restarted"] is False


# ─────────────────────────── monkey_launch v5.0.1 ───────────────────────────

def _load_launch():
    return _load("monkey_launch_v501", "monkey_launch/v5.0.1/monkey_launch.py")


def _run_launch(monkeypatch, *, aimwd_present, watchdog_already=False, max_wait=1):
    """watchdog_already=False 时模拟'首次未见 → 启动后 post-check 可见'。"""
    mod = _load_launch()
    captured: dict = {}
    patterns: list[str] = []
    state = {"sh_calls": 0}

    monkeypatch.setattr(mod, "device_serial", lambda: "S")
    monkeypatch.setattr(mod, "params", lambda: {"max_wait_seconds": max_wait})
    monkeypatch.setattr(mod, "_shell", lambda serial, cmd, timeout=30: (0, ""))

    def fake_ps(serial, pattern, timeout=10):
        patterns.append(pattern)
        if pattern == "MonkeyTest.sh":
            state["sh_calls"] += 1
            return True if watchdog_already else state["sh_calls"] > 1
        if pattern == "MonkeyWatchdog":
            return aimwd_present
        return False

    monkeypatch.setattr(mod, "_ps_grep", fake_ps)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

    def fake_output_result(success, error_message=None, metrics=None):
        captured.update(
            {"success": success, "error_message": error_message, "metrics": metrics or {}}
        )

    monkeypatch.setattr(mod, "output_result", fake_output_result)

    exited = False
    try:
        mod.main()
    except SystemExit:
        exited = True
    return captured, exited, patterns


def test_launch_fails_when_aimwd_never_runs(monkeypatch):
    out, exited, patterns = _run_launch(monkeypatch, aimwd_present=False)

    assert "MonkeyWatchdog" in patterns
    assert exited is True
    assert out["success"] is False
    assert "MonkeyWatchdog" in out["error_message"]


def test_launch_success_when_both_watchdogs_run(monkeypatch):
    out, exited, _patterns = _run_launch(monkeypatch, aimwd_present=True)

    assert exited is False
    assert out["success"] is True
    assert out["metrics"]["aimwd_started"] is True
    assert out["metrics"]["watchdog_started"] is True


def test_launch_already_running_is_idempotent(monkeypatch):
    out, exited, _patterns = _run_launch(
        monkeypatch, aimwd_present=False, watchdog_already=True
    )
    # watchdog 已在跑 → 早退，不再启动/验证 aimwd（保持既有幂等语义）
    assert exited is False
    assert out["success"] is True
    assert out["metrics"]["already_running"] is True


# ─────────────────────────── monkey_launch v5.0.2 ───────────────────────────

def _load_launch_v502():
    return _load("monkey_launch_v502", "monkey_launch/v5.0.2/monkey_launch.py")


def _run_launch_timed(monkeypatch, mod, *, max_wait=15):
    """Simulate wall clock: sh 在窗口末段才出现，aimwd 需二次 poll（各 +2s）。"""
    captured: dict = {}
    clock = {"t": 1000.0}
    aimwd_calls = 0
    t0 = clock["t"]

    monkeypatch.setattr(mod, "device_serial", lambda: "S")
    monkeypatch.setattr(
        mod, "params", lambda: {"max_wait_seconds": max_wait},
    )
    monkeypatch.setattr(mod, "_shell", lambda serial, cmd, timeout=30: (0, ""))
    monkeypatch.setattr(mod.time, "time", lambda: clock["t"])
    monkeypatch.setattr(
        mod.time,
        "sleep",
        lambda secs: clock.__setitem__("t", clock["t"] + secs),
    )

    def fake_ps(serial, pattern, timeout=10):
        if pattern == "MonkeyTest.sh":
            # 第 14s 才可见（max_wait=15 的窗口末段）
            return clock["t"] >= t0 + max_wait - 1
        if pattern == "MonkeyWatchdog":
            nonlocal aimwd_calls
            aimwd_calls += 1
            return aimwd_calls >= 2
        return False

    monkeypatch.setattr(mod, "_ps_grep", fake_ps)

    def fake_output_result(success, error_message=None, metrics=None):
        captured.update(
            {"success": success, "error_message": error_message, "metrics": metrics or {}}
        )

    monkeypatch.setattr(mod, "output_result", fake_output_result)

    exited = False
    try:
        mod.main()
    except SystemExit:
        exited = True
    return captured, exited


def test_launch_v501_shared_deadline_fails_when_sh_late(monkeypatch):
    """#1711 回归：v5.0.1 共用 deadline，sh 迟到后 aimwd 二次 poll 无预算。"""
    mod = _load_launch()
    out, exited = _run_launch_timed(monkeypatch, mod)

    assert exited is True
    assert out["success"] is False
    assert "MonkeyWatchdog" in (out.get("error_message") or "")


def test_launch_v502_independent_aimwd_window_succeeds_when_sh_late(monkeypatch):
    """#1711：v5.0.2 aimwd 独立窗口，sh 迟到 + aimwd 需二次 poll 仍成功。"""
    mod = _load_launch_v502()
    out, exited = _run_launch_timed(monkeypatch, mod)

    assert exited is False
    assert out["success"] is True
    assert out["metrics"]["aimwd_started"] is True
    assert out["metrics"]["watchdog_started"] is True


# ─────────────────────── monkey_resource_push v1.0.1 ───────────────────────

def _run_resource_push(
    monkeypatch, tmp_path, *, aimwd="file", push_ok=True, file_exists=True, file_size=100
):
    mod = _load(
        "monkey_resource_push_v101", "monkey_resource_push/v1.0.1/monkey_resource_push.py"
    )
    bundle = _bundle(tmp_path, aimwd=aimwd)
    captured: dict = {}
    pushed: list[str] = []

    monkeypatch.setattr(mod, "device_serial", lambda: "S")
    monkeypatch.setattr(mod, "params", lambda: {"aimonkey_dir": str(bundle)})
    monkeypatch.setattr(
        mod, "_resolve_aimonkey_dir", lambda cfg: Path(cfg["aimonkey_dir"])
    )
    monkeypatch.setattr(mod, "_shell", lambda serial, cmd, timeout=30: (0, ""))

    def fake_push(serial, local, remote, timeout=60):
        if not push_ok and remote.endswith("/aimwd"):
            return False
        pushed.append(remote)
        return True

    monkeypatch.setattr(mod, "_push", fake_push)
    monkeypatch.setattr(mod, "_file_exists", lambda serial, path: file_exists)
    monkeypatch.setattr(mod, "_file_size", lambda serial, path: file_size)

    def fake_output_result(success, error_message=None, metrics=None):
        captured.update(
            {"success": success, "error_message": error_message, "metrics": metrics or {}}
        )

    monkeypatch.setattr(mod, "output_result", fake_output_result)

    exited = False
    try:
        mod.main()
    except SystemExit:
        exited = True
    return captured, exited, pushed


def test_resource_push_includes_aimwd_in_required_files(monkeypatch, tmp_path):
    out, exited, pushed = _run_resource_push(monkeypatch, tmp_path)

    assert "/data/local/tmp/aimwd" in pushed
    assert exited is False
    assert out["success"] is True


def test_resource_push_missing_aimwd_fails(monkeypatch, tmp_path):
    out, exited, pushed = _run_resource_push(monkeypatch, tmp_path, aimwd="none")

    assert "/data/local/tmp/aimwd" not in pushed
    assert exited is True
    assert out["success"] is False
    assert "aimwd" in out["error_message"]


def test_resource_push_aimwd_push_failure_fails(monkeypatch, tmp_path):
    out, exited, _pushed = _run_resource_push(monkeypatch, tmp_path, push_ok=False)

    assert exited is True
    assert out["success"] is False
    assert "push failed: aimwd" in out["error_message"]


def test_resource_push_aimwd_missing_on_device_fails(monkeypatch, tmp_path):
    out, exited, _pushed = _run_resource_push(monkeypatch, tmp_path, file_exists=False)

    assert exited is True
    assert out["success"] is False
    assert "missing: /data/local/tmp/aimwd" in out["error_message"]


def test_resource_push_aimwd_zero_size_fails(monkeypatch, tmp_path):
    out, exited, _pushed = _run_resource_push(monkeypatch, tmp_path, file_size=0)

    assert exited is True
    assert out["success"] is False
    assert "zero-size: /data/local/tmp/aimwd" in out["error_message"]
