"""#2846：powercycle_setup **v1.2.1**——prefs 读取改 root 能力，「读空」不再当删除证据。

根因：AutoTestTool 是 platform 签名 system app（shared uid `android.uid.system`），
AOSP 对 non-debuggable / shared-uid 包恒拒绝 `run-as` ⇒ v1.2.0 及以前
`get_prefs_xml()` 恒空 ⇒ `repair_prefs_ownership()` 在每次 `set_prefs` /
`set_stop_flags` 前 `rm -f` 掉健康 prefs（续跑 current_count 归零、auto_resume 断链）。

本文件钉住 v1.2.1 的四条新语义 + v1.2.0 的对照锚点（旧版本不可变，只读断言）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = REPO_ROOT / "backend" / "agent" / "scripts"
_PREFS_PATH = "/data/data/com.tinno.autotesttool/shared_prefs/powercycle_runner.xml"


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


@pytest.fixture(scope="module")
def lib_v121():
    return _load("powercycle_lib_v121", "powercycle_setup/v1.2.1/_lib.py")


@pytest.fixture(scope="module")
def lib_v120():
    """对照锚点：v1.2.0 不可变，只读加载以钉住 bug 形态。"""
    return _load("powercycle_lib_v120_anchor", "powercycle_setup/v1.2.0/_lib.py")


def _install_fake_adb(monkeypatch, mod, routes: list[tuple[str, tuple]]):
    """按命令前缀派发假 adb；返回全部调用记录（args 列表）。"""
    calls: list[list] = []

    def fake_adb(*args, timeout=60):
        calls.append(list(args))
        cmd = str(args[1]) if len(args) > 1 else ""
        for prefix, result in routes:
            if cmd.startswith(prefix):
                return result
        return (0, "", "")

    monkeypatch.setattr(mod, "adb", fake_adb)
    return calls


def _rm_calls(calls: list[list]) -> list[list]:
    return [c for c in calls if len(c) > 1 and str(c[1]).startswith("rm -f")]


class TestV121NoFalseRepair:
    def test_root_readable_prefs_are_never_deleted(self, lib_v121, monkeypatch):
        """root 能读到 prefs（正常形态）⇒ 一次 rm 都不发（v1.2.0 会删）。"""
        monkeypatch.setattr(lib_v121, "is_root", lambda: True)
        calls = _install_fake_adb(monkeypatch, lib_v121, [
            ("cat " + _PREFS_PATH, (0, "<map>\n</map>", "")),
        ])

        lib_v121.repair_prefs_ownership()

        assert _rm_calls(calls) == [], f"健康 prefs 被误删：{_rm_calls(calls)}"
        # 读路径必须走 root cat（含完整路径），不是 run-as
        assert any(str(c[1]).startswith("cat " + _PREFS_PATH) for c in calls if len(c) > 1)

    def test_absent_file_does_not_trigger_delete(self, lib_v121, monkeypatch):
        """读空 + `test -f` 判定 absent ⇒ 不删（本来就没有，删无意义）。"""
        monkeypatch.setattr(lib_v121, "is_root", lambda: True)
        calls = _install_fake_adb(monkeypatch, lib_v121, [
            ("cat " + _PREFS_PATH, (1, "", "No such file or directory")),
            ("if [ -f ", (0, "absent", "")),
        ])

        lib_v121.repair_prefs_ownership()

        assert _rm_calls(calls) == []

    def test_probe_unknown_does_not_delete(self, lib_v121, monkeypatch):
        """存在性探测失败（rc≠0 / 输出不可解析）⇒ 保守不删。"""
        monkeypatch.setattr(lib_v121, "is_root", lambda: True)
        calls = _install_fake_adb(monkeypatch, lib_v121, [
            ("cat " + _PREFS_PATH, (0, "", "Permission denied")),
            ("if [ -f ", (127, "", "sh: not found")),
        ])

        lib_v121.repair_prefs_ownership()

        assert _rm_calls(calls) == []

    def test_non_root_never_deletes(self, lib_v121, monkeypatch):
        """无 root：连存在性探测都不做，直接返回（删除能力只在 root 下才有意义）。"""
        monkeypatch.setattr(lib_v121, "is_root", lambda: False)
        calls = _install_fake_adb(monkeypatch, lib_v121, [
            ("run-as ", (0, "", "run-as: package not debuggable")),
        ])

        lib_v121.repair_prefs_ownership()

        assert _rm_calls(calls) == []
        assert not any("if [ -f " in str(c[1]) for c in calls if len(c) > 1)


class TestV121GenuineRepairStillWorks:
    def test_present_but_unreadable_is_repaired_once(self, lib_v121, monkeypatch):
        """文件确实存在但连 root 都读不到（真损坏/属主异常）⇒ 删一次重建。"""
        monkeypatch.setattr(lib_v121, "is_root", lambda: True)
        calls = _install_fake_adb(monkeypatch, lib_v121, [
            ("cat " + _PREFS_PATH, (0, "", "Permission denied")),
            ("if [ -f ", (0, "present", "")),
        ])

        lib_v121.repair_prefs_ownership()

        assert _rm_calls(calls) == [["shell", f"rm -f {_PREFS_PATH}"]]


class TestV120Anchor:
    def test_v120_deletes_healthy_prefs_when_run_as_is_denied(
        self, lib_v120, monkeypatch
    ):
        """对照锚点：v1.2.0 在「run-as 恒被拒」的现场形态下会 rm 健康 prefs。

        该版本已发布、不可原地修改——本用例只读断言其行为，证明 v1.2.1 的改动
        正是差异来源（而不是环境差异）。
        """
        monkeypatch.setattr(lib_v120, "is_root", lambda: True)
        calls = _install_fake_adb(monkeypatch, lib_v120, [
            ("run-as ", (1, "", "run-as: package not debuggable")),
        ])

        lib_v120.repair_prefs_ownership()

        assert _rm_calls(calls) == [["shell", f"rm -f {_PREFS_PATH}"]], (
            "v1.2.0 的误删形态未复现——对照锚点失效，请复核加载的版本"
        )
