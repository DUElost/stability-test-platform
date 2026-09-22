"""clear_recents v1.0.5（#3107）：`dump_path` 参数校验。

`dump_path` 来自计划参数（STP_STEP_PARAMS）并被插进设备端 shell（`rm -f` / `cat`
/ uiautomator 重定向），且流程以 root 执行：`"/"` ⇒ 设备端 `rm -f /`；含空格/`;`/
`$()` 的值可扩张成任意命令。v1.0.4（#3104，读失败重试）由 #3119 先行合入，故本
校验落在 v1.0.5。#3104 的行为测试在 `test_clear_recents_v104.py`（不在此重复）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "agent" / "scripts" / "clear_recents"


def _load_v105():
    d = _SCRIPTS / "v1.0.5"
    spec = importlib.util.spec_from_file_location("_adb_cr105", d / "_adb.py")
    assert spec and spec.loader
    adb_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adb_mod)
    sys.modules["_adb"] = adb_mod
    spec2 = importlib.util.spec_from_file_location("clear_recents_105", d / "clear_recents.py")
    assert spec2 and spec2.loader
    mod = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(mod)
    sys.modules.pop("_adb", None)
    return mod


@pytest.mark.parametrize(
    "bad",
    ["/", "//", "/data", "/data/local/tmp/../etc/x", "/data/local/tmp/.",
     "/data/local/tmp/..", "/sdcard/x.xml", "/data/local/tmp/x; rm -rf /",
     "/data/local/tmp/a b.xml", 42],
)
def test_invalid_dump_path_rejected(bad):
    mod = _load_v105()
    with pytest.raises(ValueError):
        mod.validated_dump_path(bad)


@pytest.mark.parametrize(
    "good",
    [None, "", "/data/local/tmp/stp_clear_recents.xml", "/data/local/tmp/a-b_c.1.xml"],
)
def test_valid_dump_path_accepted(good):
    mod = _load_v105()
    assert mod.validated_dump_path(good).startswith("/data/local/tmp/")


def test_invalid_dump_path_fails_step_without_touching_device(monkeypatch, capsys):
    """非法 dump_path ⇒ 直接红，且**不得**下发任何含该值的命令。"""
    mod = _load_v105()
    issued: list[str] = []

    class _Proc:
        returncode = 0
        stdout = '<hierarchy rotation="0"></hierarchy>'
        stderr = ""

    def fake_quiet(command: str, timeout: int = 30):
        issued.append(command)
        return _Proc()

    monkeypatch.setattr(mod, "adb_shell", lambda command, timeout=30: issued.append(command) or "")
    monkeypatch.setattr(mod, "adb_shell_quiet", fake_quiet)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    monkeypatch.setenv("STP_DEVICE_SERIAL", "TESTSERIAL")
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({"dump_path": "/"}))
    mod.main()
    out = capsys.readouterr().out.strip().splitlines()
    payload = json.loads(out[-1])
    assert payload["success"] is False
    assert "dump_path" in payload["error_message"]
    assert not any("rm -f /" == c.strip() for c in issued), issued
    assert not any(c.startswith("uiautomator dump /") for c in issued), issued


def test_v104_had_no_validation_this_is_the_regression_pin():
    """对照锚点：v1.0.4 没有校验函数（旧版本不可变，只读断言）。"""
    d = _SCRIPTS / "v1.0.4"
    text = (d / "clear_recents.py").read_text(encoding="utf-8")
    assert "validated_dump_path" not in text
