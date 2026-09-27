"""#3463 G3：clean_env v1.1.1 的 plan 参数插入 root shell 校验 + `_adb.py` 宽容解码。

clean_env 的 `log_dirs` 与 monkey_setup v2.3.10（#3107）缺陷**完全同形**、此前无 issue
跟踪（#3463 §8.2）：`[""]` ⇒ `rm -rf /*`，`;`/空格/`$()` 可扩张成任意 root 命令。
v1.1.1 port monkey_setup v2.3.11 的 `validated_log_dirs`；`pm uninstall` 包名与
`setprop` 键按 `^[A-Za-z0-9._-]+$` 白名单、值过 `shlex.quote`；非法值整步转红。
F2（#3069 形态）：`_adb.py` 的 `text=True` 严格解码同批收口。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shlex
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "agent" / "scripts" / "clean_env"

BROKEN_DEVICE_OUTPUT = b"\x02\xf9 mobilelog dump\n"


def _load(tag: str):
    d = _SCRIPTS  # ADR-0051 Phase 3：族树即最新版本（tag 只作模块名标签）
    spec = importlib.util.spec_from_file_location(f"_adb_{tag}", d / "_adb.py")
    assert spec and spec.loader
    adb_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adb_mod)
    sys.modules["_adb"] = adb_mod
    spec2 = importlib.util.spec_from_file_location(f"clean_env_{tag}", d / "clean_env.py")
    assert spec2 and spec2.loader
    mod = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(mod)
    sys.modules.pop("_adb", None)
    return mod, adb_mod


@pytest.fixture()
def ce(monkeypatch):
    monkeypatch.setenv("STP_DEVICE_SERIAL", "TESTSERIAL")
    monkeypatch.delenv("STP_STEP_PARAMS", raising=False)
    monkeypatch.delenv("STP_ADB_PATH", raising=False)
    return _load("ce_v111")


class _Proc:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _fake_quiet(issued: list[str]):
    def run(command: str, timeout: int = 30) -> _Proc:
        issued.append(command)
        out = "Success" if command.startswith("pm uninstall") else ""
        return _Proc(stdout=out)

    return run


@pytest.mark.parametrize(
    "bad",
    [
        [""],                                   # ⇒ rm -rf /*
        ["/"],                                  # ⇒ rm -rf //*
        ["/data"],                              # 必须有 /data/ 下的子路径
        ["/data/local/tmp/*"],                  # 通配扩张删除面
        [" ; rm -rf /system ; "],               # 拼接任意 root 命令
        ["/data/aee_exp extra"],                # 带空格
        ["/data/../etc"],                       # 上跳
        ["/data/aee_exp/.."],
        ["/data/" + "x" * 121],                 # 超长（>120 尾段）
        ["relative/dir"],
        "not-a-list",
        [123],
    ],
)
def test_invalid_log_dirs_rejected(ce, bad):
    mod, _ = ce
    with pytest.raises(ValueError):
        mod.validated_log_dirs(bad)


@pytest.mark.parametrize(
    "good",
    [
        None,  # 未传 ⇒ 默认目录
        [],
        ["/data/aee_exp"],
        ["/data/vendor/aee_exp", "/data/debuglogger/mobilelog"],
        ["/sdcard/logs"],
    ],
)
def test_valid_log_dirs_accepted(ce, good):
    mod, _ = ce
    assert isinstance(mod.validated_log_dirs(good), list)


def test_default_dirs_are_themselves_valid(ce):
    """默认值必须过自己的白名单（否则未传参数就红）。"""
    mod, _ = ce
    assert mod.validated_log_dirs(None) == list(mod._DEFAULT_LOG_DIRS)
    assert mod.validated_log_dirs(list(mod._DEFAULT_LOG_DIRS)) == list(mod._DEFAULT_LOG_DIRS)


@pytest.mark.parametrize("bad", ["", "com.foo; rm -rf /", "com.foo bar", "$(reboot)", "com/foo"])
def test_invalid_package_name_rejected(ce, bad):
    mod, _ = ce
    with pytest.raises(ValueError):
        mod.validated_package_name(bad)


@pytest.mark.parametrize("good", ["com.transsion.foo_bar-1", "a"])
def test_valid_package_name_accepted(ce, good):
    mod, _ = ce
    assert mod.validated_package_name(good) == good


@pytest.mark.parametrize("bad", ["", "persist.sys.a b", "ro.x; reboot", "k$(1)"])
def test_invalid_property_key_rejected(ce, bad):
    mod, _ = ce
    with pytest.raises(ValueError):
        mod.validated_property_key(bad)


@pytest.mark.parametrize("good", ["persist.sys.stp.flag", "debug.stp-x.y"])
def test_valid_property_key_accepted(ce, good):
    mod, _ = ce
    assert mod.validated_property_key(good) == good


def _run_main(mod, monkeypatch, capsys, params: dict) -> dict:
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps(params))
    mod.main()
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_invalid_log_dirs_fails_step_without_issuing_rm(ce, monkeypatch, capsys):
    """§4 G3：非法值整步转红，且不得下发任何 rm/mkdir（破坏面不交给参数）。"""
    mod, _ = ce
    issued: list[str] = []
    monkeypatch.setattr(mod, "adb_shell_quiet", _fake_quiet(issued))
    payload = _run_main(mod, monkeypatch, capsys, {"clear_logs": True, "log_dirs": [""]})
    assert payload["success"] is False
    assert "log_dirs" in payload["error_message"]
    assert issued == []


def test_invalid_package_fails_step_without_issuing_pm(ce, monkeypatch, capsys):
    mod, _ = ce
    issued: list[str] = []
    monkeypatch.setattr(mod, "adb_shell_quiet", _fake_quiet(issued))
    payload = _run_main(
        mod, monkeypatch, capsys, {"uninstall_packages": ["com.foo; rm -rf /"]}
    )
    assert payload["success"] is False
    assert "uninstall_packages" in payload["error_message"]
    assert issued == []


def test_invalid_property_key_fails_step_without_issuing_setprop(ce, monkeypatch, capsys):
    mod, _ = ce
    issued: list[str] = []
    monkeypatch.setattr(mod, "adb_shell_quiet", _fake_quiet(issued))
    payload = _run_main(mod, monkeypatch, capsys, {"set_properties": {"ro.x; reboot": "1"}})
    assert payload["success"] is False
    assert "set_properties" in payload["error_message"]
    assert issued == []


def test_valid_params_run_and_property_value_is_quoted(ce, monkeypatch, capsys):
    """合法值照常执行（护栏不能把正常路径一起拒掉）；setprop 值经 shlex.quote。"""
    mod, _ = ce
    issued: list[str] = []
    monkeypatch.setattr(mod, "adb_shell_quiet", _fake_quiet(issued))
    payload = _run_main(
        mod,
        monkeypatch,
        capsys,
        {
            "uninstall_packages": ["com.foo"],
            "clear_logs": True,
            "log_dirs": ["/data/aee_exp"],
            "set_properties": {"persist.sys.a": "b; c"},
        },
    )
    assert payload["success"] is True
    assert payload["metrics"] == {"uninstalled": 1, "logs_cleared": 1, "properties_set": 1}
    assert "pm uninstall com.foo" in issued
    assert "rm -rf /data/aee_exp/*" in issued
    assert "mkdir -p /data/aee_exp" in issued
    assert f"setprop persist.sys.a {shlex.quote('b; c')}" in issued, issued


def test_property_value_metachars_never_expand(ce, monkeypatch, capsys):
    """值侧注入面收口：`;`/空格/`$()` 全部被 quote 包裹，单条命令、无二次求值。"""
    mod, _ = ce
    issued: list[str] = []
    monkeypatch.setattr(mod, "adb_shell_quiet", _fake_quiet(issued))
    payload = _run_main(
        mod,
        monkeypatch,
        capsys,
        {"set_properties": {"persist.sys.a": "x y; rm -rf /sdcard", "persist.sys.b": "$(reboot)"}},
    )
    assert payload["success"] is True
    assert payload["metrics"]["properties_set"] == 2
    for value in ("x y; rm -rf /sdcard", "$(reboot)"):
        cmd = f"setprop persist.sys.a {shlex.quote(value)}" if value.startswith("x") else \
              f"setprop persist.sys.b {shlex.quote(value)}"
        assert cmd in issued, issued


def _write_fake_adb(tmp_path: Path, stdout_bytes: bytes) -> str:
    script = tmp_path / "fake_adb.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        f"import sys\nsys.stdout.buffer.write({stdout_bytes!r})\n",
        encoding="utf-8",
    )
    os.chmod(script, 0o755)
    return str(script)


def test_adb_helper_tolerates_broken_device_bytes(ce, tmp_path, monkeypatch):
    """F2（#3069 形态）反例：坏字节严格解码必抛；改宽容解码后不得炸掉 helper。"""
    _, adb_mod = ce
    with pytest.raises(UnicodeDecodeError):
        BROKEN_DEVICE_OUTPUT.decode("utf-8")
    fake = _write_fake_adb(tmp_path, BROKEN_DEVICE_OUTPUT)
    monkeypatch.setenv("STP_ADB_PATH", fake)
    out = adb_mod.adb_shell("whatever")
    assert "\ufffd" in out and "mobilelog dump" in out
    res = adb_mod.adb_shell_quiet("whatever")
    assert isinstance(res.stdout, str) and "\ufffd" in res.stdout


@pytest.mark.parametrize("family", ["clean_env", "fill_storage"])
def test_adb_helpers_no_longer_decode_strictly(family):
    """静态守卫：两族 `_adb.py` 不得再出现调用形态的严格解码（#3069 崩溃入口）。"""
    src = (_SCRIPTS.parent / family / "_adb.py").read_text(encoding="utf-8")
    assert "text=True," not in src
    assert "capture_output=True, text=True," not in src
