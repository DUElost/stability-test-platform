"""flash_preflight v1.0.1：Debian 13 t64 改名兼容。

v1.0.0 在 .66 生产首跑（Run #234）误报 "qt-libs: apt install failed
rc=0"——libglib2.0-0 在 Debian 13 实装名为 libglib2.0-0t64，apt 装别名
rc=0 但按字面名复查永远失败。本版探测逻辑：字面名未命中时追加 t64
变体。v1.0.0 其余行为由既有测试覆盖。
"""

from __future__ import annotations

import importlib.util
import json
import time as real_time
from pathlib import Path


_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "agent" / "scripts" / "flash_preflight" / "v1.0.1"
)

spec = importlib.util.spec_from_file_location(
    "flash_preflight_v101", _SCRIPT_DIR / "flash_preflight.py"
)
pf = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(pf)


class _FakeTime:
    def __init__(self):
        self.sleeps: list = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)

    def time(self):
        return real_time.time()

    def monotonic(self):
        return real_time.monotonic()


class _P:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class _DpkgOnly:
    """只路由 dpkg-query；libglib2.0-0 是过渡包（字面名未装、t64 承载）。"""

    def __init__(self, *, t64_installed=True, others_installed=True):
        self.t64_installed = t64_installed
        self.others_installed = others_installed

    def __call__(self, argv, **kwargs):
        if argv[:1] == ["dpkg-query"]:
            pkg = argv[-1]
            if pkg == "libglib2.0-0":
                ok = False                      # 字面名恒为过渡包状态
            elif pkg == "libglib2.0-0t64":
                ok = self.t64_installed
            else:
                ok = self.others_installed
            return _P(stdout=("install ok installed" if ok
                              else "deinstall ok config-files"),
                      returncode=0 if ok else 1)
        return _P()


def test_t64_variant_counts_as_installed(monkeypatch, capsys, tmp_path):
    """字面名是过渡包（未装）、t64 变体已装 → 判为已装，零修复。"""
    stub = _DpkgOnly(t64_installed=True, others_installed=True)
    monkeypatch.setattr(pf.subprocess, "run", stub)
    monkeypatch.setattr(pf, "_user_in_dialout", lambda: True)
    monkeypatch.setattr(pf, "_udev_rule_ok", lambda rd: True)
    monkeypatch.setattr(pf, "_sudo_sh", lambda cmd: (0, ""))
    exe = tmp_path / "flash_tool"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setattr(pf, "_locate_flashtool", lambda: str(exe))
    fw = tmp_path / "firmware" / "MLD"
    fw.mkdir(parents=True)
    (fw / "latest.json").write_text('{"version": "9.9.9.9"}',
                                    encoding="utf-8")
    monkeypatch.setenv("STP_NFS_ROOT", str(tmp_path))
    monkeypatch.setattr(pf, "time", _FakeTime())
    pf.main()
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    qt = next(i for i in payload["metrics"]["items"]
              if i["check"] == "qt-libs")
    assert qt["ok"] is True
    assert qt["fixed"] is False
    assert payload["success"] is True


def test_missing_t64_variant_still_remediates(monkeypatch, capsys, tmp_path):
    """字面名与 t64 都缺 → 走 apt 安装路径。"""
    calls: list = []

    class Stub(_DpkgOnly):
        def __call__(self, argv, **kwargs):
            if argv[:2] == ["sudo", "-n"]:
                cmd = argv[-1]
                calls.append(cmd)
                if "apt-get install" in cmd:
                    # 模拟安装成功：t64 与其余四包全部变为已装
                    self.t64_installed = True
                    self.others_installed = True
                return _P()
            return super().__call__(argv, **kwargs)

    stub = Stub(t64_installed=False, others_installed=False)
    monkeypatch.setattr(pf.subprocess, "run", stub)
    monkeypatch.setattr(pf, "_user_in_dialout", lambda: True)
    monkeypatch.setattr(pf, "_udev_rule_ok", lambda rd: True)
    monkeypatch.setattr(
        pf, "_sudo_sh",
        lambda cmd: (lambda p: (p.returncode,
                                ((p.stdout or "") + (p.stderr or ""))
                                .strip()[-300:]))(
            stub(["sudo", "-n", "sh", "-c", cmd])))
    exe = tmp_path / "flash_tool"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setattr(pf, "_locate_flashtool", lambda: str(exe))
    fw = tmp_path / "firmware" / "MLD"
    fw.mkdir(parents=True)
    (fw / "latest.json").write_text('{"version": "9.9.9.9"}',
                                    encoding="utf-8")
    monkeypatch.setenv("STP_NFS_ROOT", str(tmp_path))
    monkeypatch.setattr(pf, "time", _FakeTime())
    pf.main()
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    qt = next(i for i in payload["metrics"]["items"]
              if i["check"] == "qt-libs")
    assert qt["ok"] is True
    assert qt["fixed"] is True
    assert any("apt-get install" in c for c in calls)


def test_unit_t64_probe_matrix(monkeypatch):
    """探测函数级矩阵：字面名过渡包+t64已装 / 都缺 / 字面名已装。"""
    monkeypatch.setattr(pf.subprocess, "run", _DpkgOnly(
        t64_installed=True))
    assert pf._check_dpkg_installed("libglib2.0-0") is True

    monkeypatch.setattr(pf.subprocess, "run", _DpkgOnly(
        t64_installed=False))
    assert pf._check_dpkg_installed("libglib2.0-0") is False

    monkeypatch.setattr(pf.subprocess, "run", _DpkgOnly(
        t64_installed=False, others_installed=True))
    # 字面名仍为过渡包状态 → 走 t64 未装 → False
    assert pf._check_dpkg_installed("libglib2.0-0") is False
