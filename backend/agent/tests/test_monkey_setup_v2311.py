"""monkey_setup v2.3.11（#3107）：`log_dirs` 参数校验。

`step_clean` 的 `clear_logs` 分支把 `log_dirs` 每一项直接插进设备端 `rm -rf {d}/*`
（流程已 root）。此前无任何校验：`[""]` 展开为 `rm -rf /*`、`["/"]` 为 `rm -rf //*`、
含空格/`;`/`$()` 的值可扩张成任意 root 命令。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "agent" / "scripts" / "monkey_setup"


def _load_version(version: str, tag: str):
    d = _SCRIPTS  # ADR-0051 Phase 3：族树即最新版本（version 只作模块名标签）
    spec = importlib.util.spec_from_file_location(f"_adb_{tag}", d / "_adb.py")
    assert spec and spec.loader
    adb_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adb_mod)
    sys.modules["_adb"] = adb_mod
    spec2 = importlib.util.spec_from_file_location(f"monkey_setup_{tag}", d / "monkey_setup.py")
    assert spec2 and spec2.loader
    mod = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(mod)
    sys.modules.pop("_adb", None)
    return mod


@pytest.mark.parametrize(
    "bad",
    [
        [""],                      # ⇒ rm -rf /*
        ["/"],                     # ⇒ rm -rf //*
        ["/data"],                 # 不是 /data/ 下的目录
        ["/data/../etc"],          # 上升
        ["/data/aee_exp; rm -rf /"],
        ["/data/aee_exp extra"],
        ["relative/dir"],
        ["/sdcard"],               # 允许 /sdcard/ 前缀，但必须有子路径
        "not-a-list",
        [123],
    ],
)
def test_invalid_log_dirs_rejected(bad):
    mod = _load_version("v2.3.11", "ms_v2311")
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
def test_valid_log_dirs_accepted(good):
    mod = _load_version("v2.3.11", "ms_v2311")
    assert isinstance(mod.validated_log_dirs(good), list)


def test_default_dirs_are_themselves_valid():
    """默认值必须过自己的白名单（否则未传参数就红）。"""
    mod = _load_version("v2.3.11", "ms_v2311")
    assert mod.validated_log_dirs(None) == list(mod._DEFAULT_LOG_DIRS)
    assert mod.validated_log_dirs(list(mod._DEFAULT_LOG_DIRS)) == list(mod._DEFAULT_LOG_DIRS)


def test_step_clean_rejects_bad_dirs_without_issuing_rm(monkeypatch):
    """非法 log_dirs ⇒ 步骤红，且不得下发任何 rm 命令（破坏性面不交给参数）。"""
    mod = _load_version("v2.3.11", "ms_v2311")
    issued: list[str] = []

    def fake_shell(command: str, timeout: int = 30) -> str:
        issued.append(command)
        return ""

    monkeypatch.setattr(mod, "adb_shell", fake_shell)
    result = mod.step_clean("TESTSERIAL", {"clear_logs": True, "log_dirs": ["/"]})

    assert result["success"] is False
    assert "log_dirs" in result["error"]
    assert not any("rm -rf" in c for c in issued), issued


def test_step_clean_uses_valid_dirs(monkeypatch):
    """合法值照常清理（护栏不能把正常路径一起拒掉）。"""
    mod = _load_version("v2.3.11", "ms_v2311")
    issued: list[str] = []
    monkeypatch.setattr(mod, "adb_shell", lambda command, timeout=30: issued.append(command) or "")
    result = mod.step_clean("TESTSERIAL", {"clear_logs": True, "log_dirs": ["/data/aee_exp"]})

    assert result["success"] is True
    assert any(c == "rm -rf /data/aee_exp/*" for c in issued), issued
    assert any(c == "mkdir -p /data/aee_exp" for c in issued), issued


