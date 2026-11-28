"""oobe_skip：boot_completed 就绪门 + 通用向导探测/禁用 + 焦点诊断。

覆盖 v1.1.0–v1.1.4：boot 门、force-stop/disable、Tinno SetupWizard 探测、
wizard_cleared 硬校验、设备侧 focus grep。
"""

from __future__ import annotations

import importlib.util
import json
import time as real_time
from pathlib import Path

import pytest


_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "agent" / "scripts" / "oobe_skip"
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

    def __init__(
        self,
        *,
        boot_completed: "list[str] | None" = None,
        readback: "dict[str, str] | None" = None,
        focus_line: str = "",
        installed_packages: "list[str] | None" = None,
        home_package: str = "com.android.launcher3",
        disabled_packages: "set[str] | None" = None,
        auto_disable: bool = True,
    ):
        self.argvs: list[list[str]] = []
        self.boot_completed = list(boot_completed or ["1"])
        self.readback = readback or {}
        self.focus_line = focus_line
        self.installed_packages = list(
            installed_packages
            if installed_packages is not None
            else [
                "com.google.android.setupwizard",
                "com.tinno.factoryoobe",
                "com.tinno.setupwizard",
            ]
        )
        self.home_package = home_package
        self.disabled_packages = set(disabled_packages or ())
        self.auto_disable = auto_disable

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

        # 单参数 shell 管道（focus / dumpsys User 0）
        if len(argv) == 5 and argv[3] == "shell" and isinstance(argv[4], str):
            script = argv[4]
            if "dumpsys window" in script and "mCurrentFocus" in script:
                return _Proc(stdout=self.focus_line)
            if "dumpsys package" in script and "User 0:" in script:
                pkg = ""
                for cand in self.installed_packages + list(self.disabled_packages):
                    if cand in script:
                        pkg = cand
                        break
                enabled = 2 if pkg in self.disabled_packages else 0
                return _Proc(stdout=f"    User 0: enabled={enabled}\n")
            return _Proc()

        if "pm" in argv and "list" in argv and "packages" in argv:
            if "-d" in argv:
                lines = [f"package:{p}" for p in sorted(self.disabled_packages)]
            else:
                lines = [f"package:{p}" for p in self.installed_packages]
            return _Proc(stdout="\n".join(lines) + ("\n" if lines else ""))

        if "pm" in argv and "path" in argv:
            pkg = argv[-1]
            if pkg in self.installed_packages:
                return _Proc(stdout=f"package:/system/app/{pkg}.apk\n")
            return _Proc(stdout="", returncode=1)

        if "pm" in argv and "disable" in argv:
            pkg = argv[-1]
            if self.auto_disable:
                self.disabled_packages.add(pkg)
                if pkg == self.home_package:
                    self.home_package = "com.android.launcher3"
                return _Proc(stdout=f"Package {pkg} new state: disabled\n")
            return _Proc(stdout="fail\n", returncode=255)

        if "pm" in argv and "disable-user" in argv:
            pkg = argv[-1]
            if self.auto_disable:
                self.disabled_packages.add(pkg)
                if pkg == self.home_package:
                    self.home_package = "com.android.launcher3"
                return _Proc(stdout=f"Package {pkg} new state: disabled-user\n")
            return _Proc(stdout="fail\n", returncode=255)

        if "resolve-activity" in argv:
            return _Proc(
                stdout=(
                    "priority=0 preferredOrder=0 match=0x108000 "
                    "specificIndex=-1 isDefault=true\n"
                    f"{self.home_package}/.Main\n"
                )
            )

        if "query-activities" in argv:
            return _Proc(
                stdout=f"packageName={self.home_package}\n"
                       "packageName=com.android.launcher3\n"
            )

        if "dumpsys" in argv and "window" in argv:
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
        assert any("am force-stop" in j for j in joined)
        assert "input keyevent 224" in joined   # 唤醒
        assert "input keyevent 82" in joined    # 解锁
        assert "input keyevent 3" in joined     # HOME

    def test_custom_suw_package_param(self, env, capsys):
        env.setenv("STP_STEP_PARAMS", json.dumps({
            "setupwizard_package": "com.example.suw",
            "extra_setupwizard_packages": [],
            "discover_setupwizard": False,
        }))
        stub = _DeviceStub(installed_packages=["com.example.suw"])
        env.setattr(oskip.subprocess, "run", stub)
        oskip.main()
        assert any("com.example.suw" in " ".join(a) for a in stub.argvs)


class TestFocusDiagnostic:
    def test_focus_on_setupwizard_recorded_not_fatal_after_clear(
            self, env, capsys):
        """清场成功后，短暂仍停在 SUW 的 focus 仅留痕，不单独判失败。"""
        stub = _DeviceStub(
            focus_line="mCurrentFocus=Window{.. "
                       "com.google.android.setupwizard/"
                       "WelcomeActivity}",
            home_package="com.android.launcher3",
        )
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
        assert payload["metrics"]["ui_focus"]["on_setupwizard"] is False
        assert "launcher3" in payload["metrics"]["ui_focus"]["line"]


class TestOobeSkip4083Fixes:
    def test_adb_root_runs_without_shell_and_polls_get_state(self, env, capsys):
        stub = _DeviceStub()
        env.setattr(oskip.subprocess, "run", stub)
        oskip.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is True
        assert ["adb", "-s", "SER1", "root"] in stub.argvs
        assert ["adb", "-s", "SER1", "shell", "root"] not in stub.argvs
        assert ["adb", "-s", "SER1", "get-state"] in stub.argvs
        assert ["adb", "-s", "SER1", "shell", "get-state"] not in stub.argvs

    def test_setup_skipped_tinno_factoryoobe_and_disable(self, env, capsys):
        stub = _DeviceStub(
            installed_packages=[
                "com.google.android.setupwizard",
                "com.tinno.factoryoobe",
            ],
            home_package="com.android.launcher3",
        )
        env.setattr(oskip.subprocess, "run", stub)
        oskip.main()
        joined = stub.shell_joined()
        assert any("settings put secure setup_skipped 1" in j for j in joined)
        assert any("am force-stop com.tinno.factoryoobe" in j for j in joined)
        assert any(
            ("pm disable com.google.android.setupwizard" in j
             or "pm disable-user --user 0 com.google.android.setupwizard" in j)
            for j in joined
        )
        assert any(
            ("pm disable com.tinno.factoryoobe" in j
             or "pm disable-user --user 0 com.tinno.factoryoobe" in j)
            for j in joined
        )

    def test_multidisplay_null_focus_ignored_and_recleared(self, env, capsys):
        calls = {"n": 0}
        stub = _DeviceStub(
            installed_packages=["com.tinno.factoryoobe"],
            home_package="com.android.launcher3",
        )

        class _Proxy:
            def __call__(self, argv, **kwargs):
                if (len(argv) == 5 and argv[3] == "shell"
                        and "dumpsys window" in str(argv[4])):
                    stub.argvs.append(argv)
                    calls["n"] += 1
                    if calls["n"] == 1:
                        return _Proc(
                            stdout="  mCurrentFocus=null\n"
                            "  mCurrentFocus=Window{1 u0 com.tinno.factoryoobe/Main}\n"
                        )
                    return _Proc(
                        stdout="  mCurrentFocus=null\n"
                        "  mCurrentFocus=Window{2 u0 com.android.launcher3/Launcher}\n"
                    )
                return stub(argv, **kwargs)

        env.setattr(oskip.subprocess, "run", _Proxy())
        oskip.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is True
        assert calls["n"] >= 2


class TestOobeSkip114GenericWizard:
    def test_discovers_tinno_setupwizard_and_writes_has_run(self, env, capsys):
        stub = _DeviceStub(
            installed_packages=["com.tinno.setupwizard"],
            home_package="com.tinno.setupwizard",
            focus_line=(
                "mCurrentFocus=Window{1 u0 "
                "com.tinno.setupwizard/.ui.WelcomeActivity}"
            ),
        )
        env.setattr(oskip.subprocess, "run", stub)
        oskip.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is True
        assert "com.tinno.setupwizard" in payload["metrics"]["wizard_packages"]
        joined = stub.shell_joined()
        assert any(
            "settings put global setup_wizard_has_run 1" in j for j in joined
        )
        assert any("am force-stop com.tinno.setupwizard" in j for j in joined)
        assert any("pm disable com.tinno.setupwizard" in j for j in joined)
        assert payload["metrics"]["wizard_cleared"]["ok"] is True
        assert payload["metrics"]["wizard_cleared"]["home_package"] == (
            "com.android.launcher3"
        )

    def test_fails_when_wizard_still_owns_home(self, env, capsys):
        stub = _DeviceStub(
            installed_packages=["com.tinno.setupwizard"],
            home_package="com.tinno.setupwizard",
            auto_disable=False,
        )
        env.setattr(oskip.subprocess, "run", stub)
        oskip.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is False
        assert "wizard_cleared" in payload["error_message"]
        assert payload["metrics"]["wizard_cleared"]["home_is_wizard"] is True

    def test_skips_missing_seed_packages(self, env, capsys):
        stub = _DeviceStub(
            installed_packages=["com.tinno.setupwizard"],
            home_package="com.tinno.setupwizard",
        )
        env.setattr(oskip.subprocess, "run", stub)
        oskip.main()
        joined = "\n".join(stub.shell_joined())
        # Google SUW 未安装 → 不得对其发 disable
        assert "pm disable com.google.android.setupwizard" not in joined
        assert "pm disable-user --user 0 com.google.android.setupwizard" not in joined
        assert "pm disable com.tinno.setupwizard" in joined
