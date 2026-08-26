"""oobe_skip v1.0.0：单台设备 OOBE 跳过。

与工位 OOBE.bat 的本质差异是本脚本的立身之本——所有命令强制
`-s <serial>` 只打目标设备（bat 对 host 上全部 adb 设备广播）。
这里重点断言 serial 隔离、无 serial 拒执行、等待与回读核验。
"""

from __future__ import annotations

import importlib.util
import json
import time as real_time
from pathlib import Path

import pytest


_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "agent" / "scripts" / "oobe_skip" / "v1.0.0"
)

spec = importlib.util.spec_from_file_location(
    "oobe_skip_v100", _SCRIPT_DIR / "oobe_skip.py"
)
oskip = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(oskip)


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class _Recorder:
    """记录全部 argv；get-state 恒 device，settings get 回读可编排。"""

    def __init__(self, readback: "dict[str, str] | None" = None):
        self.argvs: list[list[str]] = []
        self.readback = readback or {}

    def __call__(self, argv, **kwargs):
        self.argvs.append(argv)
        if "get-state" in argv:
            return _Proc(stdout="device\n")
        if len(argv) >= 6 and argv[4:6] == ["settings", "get"]:
            key = argv[-1]
            return _Proc(stdout=self.readback.get(key, "1") + "\n")
        return _Proc()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("STP_DEVICE_SERIAL", "SER1")
    monkeypatch.setenv("STP_ADB_PATH", "adb")
    monkeypatch.delenv("STP_STEP_PARAMS", raising=False)
    fake_time = type("T", (), {
        "sleeps": [],
        "sleep": classmethod(lambda cls, s: cls.sleeps.append(s)),
        "time": staticmethod(real_time.time),
        "monotonic": staticmethod(real_time.monotonic),
    })
    monkeypatch.setattr(oskip, "time", fake_time)
    return monkeypatch


def test_every_command_scoped_to_serial(env, capsys):
    rec = _Recorder()
    env.setattr(oskip.subprocess, "run", rec)
    oskip.main()
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["success"] is True
    # 除 get-state 外的每条 adb 命令都必须是 [adb, -s, SER1, shell, ...]
    for argv in rec.argvs:
        if "get-state" in argv:
            assert argv[:3] == ["adb", "-s", "SER1"]
            continue
        assert argv[:4] == ["adb", "-s", "SER1", "shell"], argv
    # 与 OOBE.bat 逐条对应
    joined = [" ".join(a[4:]) for a in rec.argvs if a[3:4] == ["shell"]]
    assert any("root" == j for j in joined)
    assert any("settings put secure user_setup_complete 1" == j
               for j in joined)
    assert any("settings put global device_provisioned 1" == j
               for j in joined)
    assert any("settings put system system_locales en-US" == j
               for j in joined)
    assert any("input keyevent 4" == j for j in joined)
    assert any(j.startswith("am start -a android.intent.action.MAIN")
               for j in joined)


def test_no_serial_refuses_blanket_execution(env, capsys):
    """没有 serial 宁可不做——这是与 OOBE.bat 广播语义的分界线。"""
    env.delenv("STP_DEVICE_SERIAL", raising=False)

    def boom(argv, **kwargs):
        raise AssertionError(f"must not touch adb: {argv}")

    env.setattr(oskip.subprocess, "run", boom)
    oskip.main()
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["success"] is False
    assert "refusing blanket execution" in payload["error_message"]


def test_device_wait_timeout_fails_without_commands(env, capsys):
    env.setenv("STP_STEP_PARAMS", json.dumps(
        {"wait_for_device_seconds": 0}))

    def never_device(argv, **kwargs):
        if "get-state" in argv:
            return _Proc(returncode=1)
        raise AssertionError(f"no commands before device ready: {argv}")

    env.setattr(oskip.subprocess, "run", never_device)
    oskip.main()
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["success"] is False
    assert "not adb-ready" in payload["error_message"]
    assert payload["metrics"]["commands"] == []


def test_verify_readback_mismatch_fails(env, capsys):
    rec = _Recorder(readback={"user_setup_complete": "0"})
    env.setattr(oskip.subprocess, "run", rec)
    oskip.main()
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["success"] is False
    vr = payload["metrics"]["verify"]
    assert vr["user_setup_complete"]["value"] == "0"
    assert vr["user_setup_complete"]["ok"] is False
    assert vr["device_provisioned"]["ok"] is True


def test_verify_disabled_skips_readback(env, capsys):
    env.setenv("STP_STEP_PARAMS", json.dumps(
        {"verify_setup_complete": False}))
    rec = _Recorder()
    env.setattr(oskip.subprocess, "run", rec)
    oskip.main()
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["success"] is True
    assert "settings get" not in json.dumps(rec.argvs)


def test_root_failure_is_not_fatal(env, capsys):
    def root_fails(argv, **kwargs):
        if "root" in argv:
            return _Proc(returncode=1, stdout="adbd cannot run as root")
        if "get-state" in argv:
            return _Proc(stdout="device\n")
        if "settings" in argv and "get" in argv:
            return _Proc(stdout="1\n")
        return _Proc()

    env.setattr(oskip.subprocess, "run", root_fails)
    oskip.main()
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["success"] is True
    assert payload["metrics"]["root"]["rc"] == 1
