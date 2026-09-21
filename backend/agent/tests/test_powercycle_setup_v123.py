"""#2979：powercycle_setup **v1.2.3**——prefs 存在性与内容共用同一次探测，「读失败」不再是删除证据。

缺陷（v1.2.1/v1.2.2 残留）：`get_prefs_xml`（cat）与 `_prefs_file_state`（test -f）
是两次独立 adb 调用——第一次 cat 撞瞬时失败（超时 rc=-1 / 设备下线）被判「读不到」，
紧接着 test -f 是**另一条命令**、成功回 present ⇒ `rm -f` 删掉健康 prefs
（续跑 current_count 归零、auto_resume 断链）。v1.2.1 只去掉了 run-as 恒空这个
必然成因，交错窗口仍在。

v1.2.3 锁定：
1. `_root_read_prefs` 单调用同源证据：ok / empty / absent / denied / transient 五态；
2. 删除只认「存在 + 读取成功 + 内容为空」这一种确定性损坏；
3. transient（rc=-1 超时）+ 文件在场 **不得**发出 rm（AC 主案）；
4. 非 root 一律不动（沿用 v1.2.1）。
对照锚点：同一「cat 失败 + present」交错下 v1.2.2 确实发出 rm——差异由本版本引入。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = REPO_ROOT / "backend" / "agent" / "scripts"
_PREFS_PATH = "/data/data/com.tinno.autotesttool/shared_prefs/powercycle_runner.xml"
_PROBE_PREFIX = "if [ ! -f "


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
def lib_v123():
    return _load("powercycle_lib_v123", "powercycle_setup/v1.2.3/_lib.py")


@pytest.fixture(scope="module")
def lib_v122():
    """对照锚点：v1.2.2 不可变，只读加载以钉住「两次调用一败一成即误删」的旧形态。"""
    return _load("powercycle_lib_v122_anchor", "powercycle_setup/v1.2.2/_lib.py")


def _install_fake_adb(monkeypatch, mod, routes: list[tuple[str, tuple]]):
    """按命令子串派发假 adb；返回全部调用记录。"""
    calls: list[list] = []

    def fake_adb(*args, timeout=60):
        calls.append([str(a) for a in args])
        cmd = str(args[1]) if len(args) > 1 else ""
        for needle, result in routes:
            if needle in cmd:
                return result
        return (0, "", "")

    monkeypatch.setattr(mod, "adb", fake_adb)
    return calls


def _rm_calls(calls: list[list]) -> list[list]:
    return [c for c in calls if len(c) > 1 and str(c[1]).startswith("rm -f")]


class TestV123SingleProbeEvidence:
    def test_transient_cat_failure_never_deletes(self, lib_v123, monkeypatch):
        """AC1 主案：probe 超时（rc=-1）⇒ 未知态，一台健康设备的一个瞬时窗口不得删文件。"""
        monkeypatch.setattr(lib_v123, "is_root", lambda: True)
        calls = _install_fake_adb(monkeypatch, lib_v123, [
            (_PROBE_PREFIX, (-1, "", "timeout")),
        ])

        lib_v123.repair_prefs_ownership()

        assert _rm_calls(calls) == [], f"瞬时失败仍被当成删除证据：{_rm_calls(calls)}"

    def test_denied_read_never_deletes(self, lib_v123, monkeypatch):
        """rc≠0 + Permission denied ⇒ denied 态，同样不删（被拒≠真读不到内容）。"""
        monkeypatch.setattr(lib_v123, "is_root", lambda: True)
        calls = _install_fake_adb(monkeypatch, lib_v123, [
            (_PROBE_PREFIX, (1, f"cat: {_PREFS_PATH}: Permission denied", "")),
        ])

        lib_v123.repair_prefs_ownership()

        assert _rm_calls(calls) == []

    def test_absent_never_deletes(self, lib_v123, monkeypatch):
        """哨兵 absent ⇒ 无可删。"""
        monkeypatch.setattr(lib_v123, "is_root", lambda: True)
        calls = _install_fake_adb(monkeypatch, lib_v123, [
            (_PROBE_PREFIX, (0, "__STP_PREFS_ABSENT__", "")),
        ])

        lib_v123.repair_prefs_ownership()

        assert _rm_calls(calls) == []

    def test_readable_prefs_never_deletes_and_reads_back(self, lib_v123, monkeypatch):
        """健康 prefs：不删，且 get_prefs_xml 从**同一次**探测读到内容。"""
        monkeypatch.setattr(lib_v123, "is_root", lambda: True)
        xml = "<map><int name=\"current_count\" value=\"7\"/></map>"
        calls = _install_fake_adb(monkeypatch, lib_v123, [
            (_PROBE_PREFIX, (0, xml, "")),
        ])

        lib_v123.repair_prefs_ownership()

        assert _rm_calls(calls) == []
        assert lib_v123.get_prefs_xml() == xml

    def test_empty_file_is_the_only_delete_evidence(self, lib_v123, monkeypatch):
        """存在 + 读取成功 + 内容空 = 确定性损坏 ⇒ 允许删（重建路径的正当输入）。"""
        monkeypatch.setattr(lib_v123, "is_root", lambda: True)
        calls = _install_fake_adb(monkeypatch, lib_v123, [
            (_PROBE_PREFIX, (0, "", "")),
        ])

        lib_v123.repair_prefs_ownership()

        rms = _rm_calls(calls)
        assert len(rms) == 1 and _PREFS_PATH in " ".join(rms[0])

    def test_repair_uses_single_adb_call(self, lib_v123, monkeypatch):
        """同源判据的结构断言：repair(ok 路径) 只发一次 shell 调用——
        不存在可错开的第二次 test -f / 独立 cat（旧缺陷的根）。"""
        monkeypatch.setattr(lib_v123, "is_root", lambda: True)
        calls = _install_fake_adb(monkeypatch, lib_v123, [
            (_PROBE_PREFIX, (0, "<map/>\n", "")),
        ])

        lib_v123.repair_prefs_ownership()

        shell_calls = [c for c in calls if len(c) > 1]
        assert len(shell_calls) == 1, f"读取/存在性又被拆成了多次调用：{shell_calls}"
        assert shell_calls[0][1].startswith(_PROBE_PREFIX)

    def test_non_root_untouched(self, lib_v123, monkeypatch):
        """非 root 一律不动（v1.2.1 语义保留）。"""
        monkeypatch.setattr(lib_v123, "is_root", lambda: False)
        calls = _install_fake_adb(monkeypatch, lib_v123, [
            (_PROBE_PREFIX, (0, "", "")),
        ])

        lib_v123.repair_prefs_ownership()

        assert _rm_calls(calls) == []


class TestV122AnchorInterleaving:
    def test_anchor_v122_deletes_healthy_prefs_on_interleaving(self, lib_v122, monkeypatch):
        """交错复现（issue 实测形态）：v1.2.2 里 cat rc=-1 + test -f present ⇒ rm。
        若此锚点变绿（不再删），说明 v1.2.2 形态变了、本文件前提需重审。"""
        monkeypatch.setattr(lib_v122, "is_root", lambda: True)
        calls = _install_fake_adb(monkeypatch, lib_v122, [
            (f"cat {_PREFS_PATH}", (-1, "", "timeout")),
            ("if [ -f ", (0, "present", "")),
        ])

        lib_v122.repair_prefs_ownership()

        assert len(_rm_calls(calls)) == 1, "锚点失效：v1.2.2 不再复现交错误删"
