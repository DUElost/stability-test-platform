"""oobe_skip v1.1.0：boot_completed 就绪门 + SUW force-stop + 焦点诊断。

v1.0.0 的 serial 隔离语义由既有用例覆盖；这里聚焦真机实证的三个增量：
get-state 放行过早导致 SUW 抢回前台（须再等 boot_completed=1）、
已在前台的 SetupWizard 必须显式 force-stop、ui_focus 诊断字段。
"""

from __future__ import annotations

import importlib.util
import json
import time as real_time
from pathlib import Path

import pytest


_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "agent" / "scripts" / "oobe_skip" / "v1.1.0"
)

spec = importlib.util.spec_from_file_location(
    "oobe_skip_v110", _SCRIPT_DIR / "oobe_skip.py"
)
oskip = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(oskip)


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class _DeviceStub:
    """可编排的 adb 桩：按 argv 片段路由响应，并记录全部命令。"""

    def __init__(self, *, boot_completed: "list[str] | None" = None,
                 readback: "dict[str, str] | None" = None,
                 focus_line: str = ""):
        self.argvs: list[list[str]] = []
        # getprop sys.boot_completed 的逐次返回（弹尽后恒 "1"）
        self.boot_completed = list(boot_completed or ["1"])
        self.readback = readback or {}
        self.focus_line = focus_line

    def __call__(self, argv, **kwargs):
        self.argvs.append(argv)
        if "get-state" in argv:
            return _Proc(stdout="device\n")
        if "getprop" in argv and "sys.boot_completed" in argv:
            value = self.boot_completed.pop(0) if self.boot_completed else "1"
            return _Proc(stdout=value + "\n")
        if len(argv) >= 6 and argv[4:6] == ["settings", "get"]:
            key = argv[-1]
            return _Proc(stdout=self.readback.get(key, "1") + "\n")
        if "dumpsys" in argv:
            return _Proc(stdout=self.focus_line)
        return _Proc()

    def shell_joined(self):
        return [" ".join(a[4:]) for a in self.argvs if a[3:4] == ["shell"]]


class _FakeTime:
    def __init__(self):
        self.sleeps: list = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)

    def time(self):
        return real_time.time()

    def monotonic(self):
        return real_time.monotonic()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("STP_DEVICE_SERIAL", "SER1")
    monkeypatch.setenv("STP_ADB_PATH", "adb")
    monkeypatch.delenv("STP_STEP_PARAMS", raising=False)
    fake_time = _FakeTime()
    monkeypatch.setattr(oskip, "time", fake_time)
    return monkeypatch


class TestBootCompletedGate:
    def test_commands_deferred_until_boot_completed(
            self, env, capsys):
        """get-state 已 device 但 boot 未完成 → 先打 boot-wait 戳不发命令。"""
        stub = _DeviceStub(boot_completed=["0", "0", "1"])
        env.setattr(oskip.subprocess, "run", stub)
        oskip.main()
        captured = capsys.readouterr()
        payload = json.loads(captured.out.strip())
        assert payload["success"] is True
        # 前两次轮询 boot=0 期间不得出现任何 shell 设置命令
        first_setup_idx = next(i for i, a in enumerate(stub.argvs)
                               if "settings" in a and "put" in a)
        polls_before = sum(1 for a in stub.argvs[:first_setup_idx]
                           if "getprop" in a)
        assert polls_before >= 2
        assert captured.err.count('"stage": "boot-wait"') == 2

    def test_boot_wait_timeout_fails_without_commands(self, env, capsys):
        env.setenv("STP_STEP_PARAMS",
                   json.dumps({"wait_for_device_seconds": 0}))
        stub = _DeviceStub(boot_completed=["0"] * 99)
        env.setattr(oskip.subprocess, "run", stub)
        oskip.main()
        captured = capsys.readouterr()
        payload = json.loads(captured.out.strip())
        assert payload["success"] is False
        assert payload["metrics"]["commands"] == []
        assert '"stage": "wait-timeout"' in captured.err
        # budget=0：get-state 已 device 但首次 boot 轮询(=0)未过即超时
        assert '"last_state": "device"' in captured.err
        assert '"boot_completed": false' in captured.err


class TestSuwForceStopAndKeys:
    def test_force_stop_and_wake_home_sequence(self, env, capsys):
        stub = _DeviceStub()
        env.setattr(oskip.subprocess, "run", stub)
        oskip.main()
        joined = stub.shell_joined()
        assert "am force-stop com.google.android.setupwizard" in joined
        assert "input keyevent 224" in joined   # 唤醒
        assert "input keyevent 82" in joined    # 解锁
        assert "input keyevent 3" in joined     # HOME

    def test_custom_suw_package_param(self, env, capsys):
        env.setenv("STP_STEP_PARAMS", json.dumps(
            {"setupwizard_package": "com.example.suw"}))
        stub = _DeviceStub()
        env.setattr(oskip.subprocess, "run", stub)
        oskip.main()
        assert any("com.example.suw" in " ".join(a) for a in stub.argvs)


class TestFocusDiagnostic:
    def test_focus_on_setupwizard_recorded_not_fatal(self, env, capsys):
        """SUW 抢回前台不判失败（标志位是权威），但 ui_focus 留痕。"""
        stub = _DeviceStub(focus_line="mCurrentFocus=Window{.. "
                           "com.google.android.setupwizard/"
                           "WelcomeActivity}")
        env.setattr(oskip.subprocess, "run", stub)
        oskip.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is True
        assert payload["metrics"]["ui_focus"]["on_setupwizard"] is True

    def test_focus_launcher_recorded(self, env, capsys):
        stub = _DeviceStub(focus_line="mCurrentFocus=Window{.. "
                           "com.android.launcher3/Launcher3QuickStepGo}")
        env.setattr(oskip.subprocess, "run", stub)
        oskip.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is True
        assert payload["metrics"]["ui_focus"]["on_setupwizard"] is False


def test_failure_hint_mentions_reflash_when_su_alive(env, capsys):
    """核验失败且 UI 在 SUW 上时，错误信息提示可能被后续重刷重置。"""
    stub = _DeviceStub(readback={"user_setup_complete": "0"},
                       focus_line="mCurrentFocus="
                       "com.google.android.setupwizard/x")
    env.setattr(oskip.subprocess, "run", stub)
    oskip.main()
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["success"] is False
    assert "re-run this step" in payload["error_message"]
