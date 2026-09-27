# -*- coding: utf-8 -*-
"""#3463 G1（sleep_setup 族）：「读空＝不存在」不得再作为删除或整写最小 map 的证据。

port 自 powercycle_setup 的 #2846/#2979 判据（同构）：
- ``_root_read_prefs`` 单次 root 探测同时给出存在性与内容（ok/empty/absent/denied/transient）；
- ``repair_prefs_ownership`` 只认「存在 + 读取成功 + 内容为空」为删除证据；
- ``set_stop_flags`` 仅 ``absent`` 整写最小 map；``transient``/``denied`` 保留文件并
  抛可重试失败（platform 签名 non-debuggable 系统 app 的 run-as 恒拒不得触发覆盖）。
测试针对族树（旧版本目录已不存在）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = REPO_ROOT / "backend" / "agent" / "scripts"
_PREFS_PATH = "/data/data/com.tinno.autotesttool/shared_prefs/sleep_test_runner.xml"
_PROBE_PREFIX = "if [ ! -f "
_RUNAS_PREFIX = "run-as "
_HEALTHY_XML = (
    "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>\n"
    "<map>\n"
    '    <int name="test_times" value="100"/>\n'
    '    <int name="current_count" value="37"/>\n'
    '    <boolean name="auto_resume" value="true"/>\n'
    '    <boolean name="running" value="true"/>\n'
    "</map>\n"
)


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
def lib():
    return _load("sleep_setup_lib_3463", "sleep_setup/_lib.py")


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


def _pusher(monkeypatch, mod) -> list[str]:
    pushed: list[str] = []
    monkeypatch.setattr(mod, "push_prefs_xml", lambda content: pushed.append(content))
    return pushed


class TestSleepSetupRepairEvidence:
    def test_root_readable_never_deletes_regardless_of_run_as(self, lib, monkeypatch):
        """①：root 可读 ⇒ 即使 run-as 恒拒也不得 rm；get_prefs_xml 同源读到内容。"""
        monkeypatch.setattr(lib, "is_root", lambda: True)
        calls = _install_fake_adb(monkeypatch, lib, [
            (_PROBE_PREFIX, (0, _HEALTHY_XML, "")),
            (_RUNAS_PREFIX, (1, "run-as: package not debuggable: com.tinno.autotesttool", "")),
        ])

        lib.repair_prefs_ownership()

        assert _rm_calls(calls) == [], f"root 可读仍发出删除：{_rm_calls(calls)}"
        assert lib.get_prefs_xml() == _HEALTHY_XML.strip()

    def test_repair_uses_single_adb_call(self, lib, monkeypatch):
        """同源判据的结构断言：repair(ok 路径) 只发一次 shell 调用——不存在可错开的第二次存在性核验。"""
        monkeypatch.setattr(lib, "is_root", lambda: True)
        calls = _install_fake_adb(monkeypatch, lib, [
            (_PROBE_PREFIX, (0, _HEALTHY_XML, "")),
        ])

        lib.repair_prefs_ownership()

        shell_calls = [c for c in calls if len(c) > 1]
        assert len(shell_calls) == 1, f"读取/存在性又被拆成了多次调用：{shell_calls}"
        assert shell_calls[0][1].startswith(_PROBE_PREFIX)

    def test_empty_file_is_the_only_delete_evidence(self, lib, monkeypatch):
        """③ 补集：存在 + 读取成功 + 内容空 = 确定性损坏 ⇒ 唯一允许 ``rm -f`` 的形态。"""
        monkeypatch.setattr(lib, "is_root", lambda: True)
        calls = _install_fake_adb(monkeypatch, lib, [
            (_PROBE_PREFIX, (0, "", "")),
        ])

        lib.repair_prefs_ownership()

        rms = _rm_calls(calls)
        assert len(rms) == 1 and _PREFS_PATH in " ".join(rms[0])

    def test_transient_and_denied_never_delete(self, lib, monkeypatch):
        """② repair 侧：transient（rc=-1）与 denied（被拒）都是未知态——保留文件。"""
        monkeypatch.setattr(lib, "is_root", lambda: True)
        for probe_result in [
            (-1, "", "timeout"),
            (1, f"cat: {_PREFS_PATH}: Permission denied", ""),
        ]:
            calls = _install_fake_adb(monkeypatch, lib, [(_PROBE_PREFIX, probe_result)])
            lib.repair_prefs_ownership()
            assert _rm_calls(calls) == [], f"probe={probe_result} 被当成删除证据"


class TestSleepSetupStopFlagsEvidence:
    def test_transient_read_does_not_write_and_is_retryable(self, lib, monkeypatch):
        """②：探测瞬态失败 ⇒ 不写任何字节、保留文件，抛可重试失败（步骤 retry 重跑）。"""
        monkeypatch.setattr(lib, "is_root", lambda: True)
        calls = _install_fake_adb(monkeypatch, lib, [
            (_PROBE_PREFIX, (-1, "", "timeout")),
        ])
        pushed = _pusher(monkeypatch, lib)

        with pytest.raises(RuntimeError) as ei:
            lib.set_stop_flags()

        assert "可重试" in str(ei.value)
        assert pushed == [], f"瞬态失败仍被整写最小 map：{pushed}"
        assert not _rm_calls(calls)

    def test_denied_read_does_not_write_and_is_retryable(self, lib, monkeypatch):
        """②：root 探测被拒（未知态）⇒ 同样保留文件 + 可重试失败。"""
        monkeypatch.setattr(lib, "is_root", lambda: True)
        _install_fake_adb(monkeypatch, lib, [
            (_PROBE_PREFIX, (1, f"cat: {_PREFS_PATH}: Permission denied", "")),
        ])
        pushed = _pusher(monkeypatch, lib)

        with pytest.raises(RuntimeError) as ei:
            lib.set_stop_flags()

        assert "可重试" in str(ei.value)
        assert pushed == []

    def test_nonroot_runas_denial_does_not_clobber(self, lib, monkeypatch):
        """②（sleep 实况主因）：non-debuggable 系统 app 的 run-as 恒拒 ≠ 文件不存在——旧实现每轮据此整写最小 map 压掉健康 prefs。"""
        monkeypatch.setattr(lib, "is_root", lambda: False)
        _install_fake_adb(monkeypatch, lib, [
            (_RUNAS_PREFIX, (1, "run-as: package not debuggable: com.tinno.autotesttool", "")),
        ])
        pushed = _pusher(monkeypatch, lib)

        with pytest.raises(RuntimeError) as ei:
            lib.set_stop_flags()

        assert "可重试" in str(ei.value)
        assert pushed == []

    def test_absent_writes_minimal_map(self, lib, monkeypatch):
        """③：仅明确不存在才整写最小 map（唯一合法的整写出口）。"""
        monkeypatch.setattr(lib, "is_root", lambda: True)
        _install_fake_adb(monkeypatch, lib, [
            (_PROBE_PREFIX, (0, "__STP_PREFS_ABSENT__", "")),
        ])
        pushed = _pusher(monkeypatch, lib)

        lib.set_stop_flags()

        assert len(pushed) == 1
        assert 'name="auto_resume" value="false"' in pushed[0]
        assert 'name="running" value="false"' in pushed[0]
        assert "test_times" not in pushed[0]      # 最小 map，非配置重建

    def test_root_readable_updates_in_place(self, lib, monkeypatch):
        """① set_stop_flags 侧：root 可读 ⇒ 在健康 prefs 上原位更新字段，current_count 不得丢。"""
        monkeypatch.setattr(lib, "is_root", lambda: True)
        _install_fake_adb(monkeypatch, lib, [
            (_PROBE_PREFIX, (0, _HEALTHY_XML, "")),
            (_RUNAS_PREFIX, (1, "run-as: package not debuggable: com.tinno.autotesttool", "")),
        ])
        pushed = _pusher(monkeypatch, lib)

        lib.set_stop_flags()

        assert len(pushed) == 1
        assert 'name="current_count" value="37"' in pushed[0]
        assert 'name="running" value="false"' in pushed[0]
        assert 'name="auto_resume" value="false"' in pushed[0]
