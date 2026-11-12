# -*- coding: utf-8 -*-
"""powercycle prefs 的 SELinux context 对齐（NX3 开关机专项，2026-11-12）。

背景：NX3（P535AA）首装时 adb push 新建的 prefs 文件落到
app_data_file:s0:c203,c256,c512,c768，而 Tinno platform 签名 APK 以
platform_app:s0:c512,c768 运行；MLS category 不匹配导致应用读 prefs
EACCES。setup 的 dumpsys 探测仍能看到 ServiceRecord，但应用读不到
running=true，重启后 BootCompletedReceiver 跳过续跑，循环停在 cycle 1。
V552AA 设备因 prefs 已由应用自建/重写而标签正确，未暴露。

本测试钉住：对齐目标取应用 data 目录 context、chcon 后回读校验、失败
必须 raise（不把「可能读不到」当成功）、非 enforcing 设备不动标签。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = REPO_ROOT / "backend" / "agent" / "scripts"
FAMILIES = ("powercycle_setup", "powercycle_check")
_CTX = "u:object_r:app_data_file:s0:c512,c768"
_DATA_DIR = "/data/data/com.tinno.autotesttool"
_PREFS_DIR = f"{_DATA_DIR}/shared_prefs"
_PREFS_FILE = f"{_PREFS_DIR}/powercycle_runner.xml"


def _load(name: str, rel_path: str):
    path = _SCRIPTS / rel_path
    sys.path.insert(0, str(path.parent))
    try:
        sys.modules.pop("_lib", None)
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader, f"cannot locate {path}"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.modules.pop("_lib", None)
        sys.path.remove(str(path.parent))


@pytest.fixture(scope="module", params=FAMILIES)
def lib(request):
    return _load(f"pc_selinux_lib_{request.param}", f"{request.param}/_lib.py")


def _install_fake_adb(monkeypatch, mod, routes):
    calls: list[list[str]] = []

    def fake_adb(*args, timeout=60):
        calls.append([str(a) for a in args])
        cmd = str(args[1]) if len(args) > 1 else ""
        for needle, result in routes:
            if needle in cmd:
                return result
        return (0, "", "")

    monkeypatch.setattr(mod, "adb", fake_adb)
    return calls


def _ctx_routes(readback_ctx: str = _CTX):
    return [
        # 先匹配更长的 shared_prefs 路径，避免被 data 目录前缀吞掉。
        (f"ls -Zd {_PREFS_DIR}", (0, f"{readback_ctx} {_PREFS_DIR}\n{readback_ctx} {_PREFS_FILE}\n", "")),
        (f"ls -Zd {_DATA_DIR}", (0, f"{_CTX} {_DATA_DIR}\n", "")),
        ("chcon", (0, "", "")),
    ]


def test_align_uses_app_data_context_and_verifies(lib, monkeypatch):
    monkeypatch.setattr(lib, "is_root", lambda: True)
    monkeypatch.setattr(lib, "adb_shell", lambda command, timeout=60: "enforcing")
    calls = _install_fake_adb(monkeypatch, lib, _ctx_routes())

    lib._align_prefs_selinux_context()

    chcon = [c for c in calls if len(c) > 1 and "chcon" in c[1]]
    assert chcon, "未发出 chcon"
    assert _CTX in chcon[0][1]
    assert _PREFS_DIR in chcon[0][1] and _PREFS_FILE in chcon[0][1]


def test_align_raises_when_chcon_fails(lib, monkeypatch):
    monkeypatch.setattr(lib, "is_root", lambda: True)
    monkeypatch.setattr(lib, "adb_shell", lambda command, timeout=60: "enforcing")
    routes = _ctx_routes()
    routes[2] = ("chcon", (1, "", "Permission denied"))
    _install_fake_adb(monkeypatch, lib, routes)

    with pytest.raises(RuntimeError) as ei:
        lib._align_prefs_selinux_context()
    assert "context 修正失败" in str(ei.value)


def test_align_raises_when_readback_mismatches(lib, monkeypatch):
    monkeypatch.setattr(lib, "is_root", lambda: True)
    monkeypatch.setattr(lib, "adb_shell", lambda command, timeout=60: "enforcing")
    _install_fake_adb(
        monkeypatch,
        lib,
        _ctx_routes(readback_ctx="u:object_r:app_data_file:s0:c203,c256,c512,c768"),
    )

    with pytest.raises(RuntimeError) as ei:
        lib._align_prefs_selinux_context()
    assert "未对齐" in str(ei.value)


def test_align_skips_on_non_enforcing(lib, monkeypatch):
    monkeypatch.setattr(lib, "is_root", lambda: True)
    monkeypatch.setattr(lib, "adb_shell", lambda command, timeout=60: "permissive")
    calls = _install_fake_adb(monkeypatch, lib, _ctx_routes())

    lib._align_prefs_selinux_context()

    assert not [c for c in calls if len(c) > 1 and "chcon" in c[1]]


def test_push_prefs_xml_invokes_alignment(lib, monkeypatch):
    monkeypatch.setattr(lib, "is_root", lambda: True)
    monkeypatch.setattr(lib, "get_app_uid", lambda: 10204)
    monkeypatch.setattr(lib, "_push_file", lambda local, remote: None)
    monkeypatch.setattr(lib, "adb_shell", lambda command, timeout=60: "")
    aligned: list[bool] = []
    monkeypatch.setattr(lib, "_align_prefs_selinux_context", lambda: aligned.append(True))

    lib.push_prefs_xml("<map></map>")

    assert aligned == [True]


def test_align_raises_on_unknown_selinux_status(lib, monkeypatch):
    """getenforce 读不到时不得当作非 enforcing 静默跳过。"""
    monkeypatch.setattr(lib, "is_root", lambda: True)
    monkeypatch.setattr(lib, "adb_shell", lambda command, timeout=60: "")
    _install_fake_adb(monkeypatch, lib, _ctx_routes())

    with pytest.raises(RuntimeError) as ei:
        lib._align_prefs_selinux_context()
    assert "SELinux 状态" in str(ei.value)


def test_align_raises_when_context_unreadable(lib, monkeypatch):
    """enforcing 下读不到 data 目录 context：不能假装对齐成功。"""
    monkeypatch.setattr(lib, "is_root", lambda: True)
    monkeypatch.setattr(lib, "adb_shell", lambda command, timeout=60: "enforcing")
    routes = _ctx_routes()
    routes[1] = (f"ls -Zd {_DATA_DIR}", (1, "", "Permission denied"))
    _install_fake_adb(monkeypatch, lib, routes)

    with pytest.raises(RuntimeError) as ei:
        lib._align_prefs_selinux_context()
    assert "context" in str(ei.value)
