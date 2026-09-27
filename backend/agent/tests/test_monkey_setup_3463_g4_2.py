"""monkey_setup v2.3.13（#3173，#3463 批次 G4-2）：残余参数注入面收口。

#3472 复核发现的三处 F3 漏面（v1.0 扫描按 rm/dd/chmod/pm/setprop 关键字漏掉
mkdir/cat/cd/tar/dumpsys/cmd wifi）：
  - `push.remote_dir`：插进 `cat {marker}` / `mkdir -p` / `cd ... && tar`（root
    shell），cfg 入口经 `validated_remote_path` 校验，非法值整步转红、零 adb 调用；
  - `install.pkg_name`：`dumpsys package {pkg_name}` 单字符串设备 shell，cfg 入口
    经 `validated_pkg_name` 校验一次；
  - `wifi.ssid/password`：port connect_wifi #816——按「单个 shell 参数」语义
    `shlex.quote`（SSID 允许任意字符，不做白名单）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tools.dev.source_anchor import SourceGuard

_SCRIPTS = Path(__file__).resolve().parents[2] / "agent" / "scripts" / "monkey_setup"


def _load(tag: str):
    """按文件加载族树（ADR-0051 Phase 3：族树即最新版本；同 G4 测试的姿势）。"""
    spec = importlib.util.spec_from_file_location(f"_adb_{tag}", _SCRIPTS / "_adb.py")
    assert spec and spec.loader
    adb_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adb_mod)
    sys.modules["_adb"] = adb_mod
    spec2 = importlib.util.spec_from_file_location(f"monkey_setup_{tag}", _SCRIPTS / "monkey_setup.py")
    assert spec2 and spec2.loader
    mod = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(mod)
    sys.modules.pop("_adb", None)
    return mod


def _patch_advancing_clock(monkeypatch, mod, *, start: float = 1_000_000.0):
    """Replace mod.time.sleep/time so sleep advances a fake clock (no wall wait).

    同构自 `test_clear_recents_v106.py` / `test_powercycle_scripts.py`（#3202 守卫：
    sleep 换 no-op 却不推进时钟 = 等待环靠真墙钟判 deadline 的忙等形态）。
    """
    state = {"now": start}

    def fake_time() -> float:
        return state["now"]

    def fake_sleep(seconds: float) -> None:
        state["now"] += float(seconds)

    monkeypatch.setattr(mod.time, "time", fake_time)
    monkeypatch.setattr(mod.time, "sleep", fake_sleep)
    return state


class _FakeQuietProc:
    """`adb_shell_quiet` 的最小假 CompletedProcess。"""

    def __init__(self, stdout: str = "") -> None:
        self.returncode = 0
        self.stdout = stdout
        self.stderr = ""


# ── ① push.remote_dir：cfg 入口校验，非法值整步转红、零 shell ────────────

def _make_bundle(tmp_path: Path) -> tuple[Path, Path, str]:
    """真实 bundle + sha 一致的 manifest——变异后的旧代码必须能走到 `cat` 那一步。"""
    bundle = tmp_path / "bundle.tar.gz"
    bundle.write_bytes(b"stp-bundle-payload\n")
    sha = hashlib.sha256(bundle.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"bundle_sha256": sha, "name": "t", "file_count": 1}), encoding="utf-8")
    return bundle, manifest, sha


@pytest.mark.parametrize(
    "bad",
    [
        "/sdcard/x;reboot",
        "/sdcard/$(id)",
        "/sdcard/../system",
        "relative/path",
        "",
    ],
)
def test_step_push_rejects_bad_remote_dir_without_any_adb(bad, monkeypatch, tmp_path):
    mod = _load("g42_rd")
    bundle, manifest, _sha = _make_bundle(tmp_path)
    issued: list[str] = []
    pushes: list = []
    monkeypatch.setattr(mod, "adb_shell", lambda command, timeout=30: issued.append(command) or "")
    monkeypatch.setattr(mod, "_push_or_timeout", lambda *a, **kw: pushes.append(a) or None)
    result = mod.step_push(
        "TESTSERIAL",
        {"bundle": str(bundle), "manifest": str(manifest), "remote_dir": bad},
    )
    assert result["success"] is False
    assert "remote" in result["error"], result
    assert issued == [], f"非法 remote_dir 不得进入任何 adb shell：{issued}"
    assert pushes == [], "校验失败必须发生在任何 push 之前"


def test_step_push_default_remote_dir_passes(monkeypatch, tmp_path):
    """默认值 /sdcard/test_resources 必须通过，且 marker 路径按原语义构造。"""
    mod = _load("g42_rd_def")
    bundle, manifest, sha = _make_bundle(tmp_path)
    issued: list[str] = []
    monkeypatch.setattr(mod, "adb_shell", lambda command, timeout=30: issued.append(command) or sha + "\n")
    result = mod.step_push(
        "TESTSERIAL",
        {"bundle": str(bundle), "manifest": str(manifest)},
    )
    assert result["success"] is True, result
    assert result.get("skipped") is True
    assert issued == [f"cat /sdcard/test_resources/.stp_bundle_sha256 2>/dev/null"], issued


def test_step_push_valid_remote_dir_flows_through(monkeypatch, tmp_path):
    mod = _load("g42_rd_ok")
    bundle, manifest, sha = _make_bundle(tmp_path)
    issued: list[str] = []
    monkeypatch.setattr(mod, "adb_shell", lambda command, timeout=30: issued.append(command) or sha + "\n")
    result = mod.step_push(
        "TESTSERIAL",
        {"bundle": str(bundle), "manifest": str(manifest), "remote_dir": "/sdcard/myres"},
    )
    assert result["success"] is True, result
    assert issued == [f"cat /sdcard/myres/.stp_bundle_sha256 2>/dev/null"], issued


# ── ② install.pkg_name：cfg 入口统一校验一次 ─────────────────────────────

@pytest.mark.parametrize("bad", ["a;reboot", "$(id)"])
def test_step_install_rejects_bad_pkg_name_without_subprocess(bad, monkeypatch):
    mod = _load("g42_pkg")
    calls: list = []
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda *a, **kw: calls.append(a) or subprocess.CompletedProcess(a, 0, b"", b""),
    )
    result = mod.step_install(
        "TESTSERIAL",
        {"apk_path": "app.apk", "pkg_name": bad, "required_version": "1.0.0"},
    )
    assert result["success"] is False
    assert "install.pkg_name" in result["error"], result
    assert calls == [], "非法 pkg_name 不得进入任何设备命令"


def test_step_install_valid_pkg_name_uses_validated_value(monkeypatch):
    mod = _load("g42_pkg_ok")
    commands: list[str] = []
    install_commands: list[list[str]] = []

    def fake_run(argv, **kwargs):
        if argv[-1].startswith("dumpsys package "):
            commands.append(argv[-1])
            return subprocess.CompletedProcess(argv, 0, "no version here", "")
        install_commands.append(argv)
        # step_install 自身的 subprocess 走 text=True 契约（G4 只改了 _adb.py）
        return subprocess.CompletedProcess(argv, 0, "Success", "")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    result = mod.step_install(
        "TESTSERIAL",
        {"apk_path": "app.apk", "pkg_name": "com.example.app", "required_version": "9.9.9"},
    )
    assert result["success"] is True, result
    assert commands == ["dumpsys package com.example.app | grep versionName"], commands
    assert install_commands, "版本不匹配应继续走安装"


# ── ③ wifi.ssid/password：shlex.quote（无字符白名单） ─────────────────────

@pytest.mark.parametrize(
    "ssid,password",
    [
        ("net$(id)", "p;wd"),
        ("a`id`b", 'p"q'),
        ("it's", "p$q"),
        ("HomeNet", "pw1"),
    ],
)
def test_step_wifi_quotes_ssid_and_password(ssid, password, monkeypatch):
    """特殊字符必须整体落在 shlex 引号内——shlex.split 逆解析应还原原始值。"""
    mod = _load("g42_wifi")
    _patch_advancing_clock(monkeypatch, mod)
    issued: list[str] = []
    monkeypatch.setattr(mod, "adb_shell_quiet", lambda command, timeout=10: _FakeQuietProc(""))
    monkeypatch.setattr(mod, "adb_shell", lambda command, timeout=30: issued.append(command) or "OK")

    result = mod.step_wifi("TESTSERIAL", {"ssid": ssid, "password": password})
    assert result["success"] is True, result
    assert "svc wifi enable" in issued
    connect = [c for c in issued if "connect-network" in c]
    assert len(connect) == 1, issued
    # 逆解析：命令切分后恰好还原出**原始** ssid/password（元字符没有脱离引号）
    tokens = shlex.split(connect[0])
    assert tokens == ["cmd", "-w", "wifi", "connect-network", ssid, "wpa2", password], connect[0]


def test_step_wifi_metachars_are_inside_quotes(monkeypatch):
    """字面断言：`$()` 等元字符出现在引号包裹的片段里，而非裸露在命令中。"""
    mod = _load("g42_wifi_lit")
    _patch_advancing_clock(monkeypatch, mod)
    monkeypatch.setattr(mod, "adb_shell_quiet", lambda command, timeout=10: _FakeQuietProc(""))
    issued: list[str] = []
    monkeypatch.setattr(mod, "adb_shell", lambda command, timeout=30: issued.append(command) or "OK")
    result = mod.step_wifi("TESTSERIAL", {"ssid": "net$(id)", "password": "p;wd"})
    assert result["success"] is True
    connect = next(c for c in issued if "connect-network" in c)
    assert "'net$(id)'" in connect, connect
    assert "'p;wd'" in connect, connect
    # 旧原形（双引号直插）不得残留
    assert 'connect-network "net$(id)"' not in connect, connect


def test_step_wifi_already_connected_check_unchanged(monkeypatch):
    """「已连接」判断保持 `ssid in status` 子串语义（不经 shell）。"""
    mod = _load("g42_wifi_conn")
    _patch_advancing_clock(monkeypatch, mod)
    monkeypatch.setattr(mod, "adb_shell_quiet", lambda command, timeout=10: _FakeQuietProc("connected to MyNet"))
    issued: list[str] = []
    monkeypatch.setattr(mod, "adb_shell", lambda command, timeout=30: issued.append(command) or "OK")
    result = mod.step_wifi("TESTSERIAL", {"ssid": "MyNet", "password": "x"})
    assert result == {"success": True, "skipped": True, "reason": "Already connected to MyNet"}
    assert issued == [], "已连接分支不得发起连接命令"


# ── 源扫描守卫（SourceGuard：锚点 → 形态） ────────────────────────────────

def test_source_guard_step_wifi_uses_shlex_quote():
    mod = _load("g42_sg_wifi")
    guard = SourceGuard.of_module(mod).anchored("def step_wifi(")
    guard.assert_absent(
        'connect-network "{ssid}"',
        why="#3173 G4-2：双引号挡不住 $()/反引号，SSID/密码必须 shlex.quote",
    )
    guard.assert_present(
        "shlex.quote(ssid)",
        why="#3173 G4-2：按单个 shell 参数语义 quote（port connect_wifi #816）",
    )


def test_source_guard_step_install_validates_pkg_name():
    mod = _load("g42_sg_install")
    guard = SourceGuard.of_module(mod).anchored("def step_install(")
    guard.assert_absent(
        'pkg_name = cfg.get("pkg_name", "")\n    required_version',
        why="#3173 G4-2：cfg 读入与使用点之间必须有校验（旧原形是零间隙直通）",
    )
    guard.assert_present(
        'validated_pkg_name(pkg_name, "install.pkg_name")',
        why="#3173 G4-2：cfg 入口统一校验一次",
    )


def test_source_guard_step_push_validates_remote_dir():
    mod = _load("g42_sg_push")
    guard = SourceGuard.of_module(mod).anchored("def step_push(")
    guard.assert_absent(
        'remote_dir = cfg.get("remote_dir", "/sdcard/test_resources").rstrip("/")',
        why="#3173 G4-2：remote_dir 直插 cat/mkdir/tar（root shell），必须先校验",
    )
    guard.assert_present(
        "validated_remote_path(",
        why="#3173 G4-2：判据与 push.files[].remote 一致（validated_remote_path）",
    )
