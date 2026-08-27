"""flash_preflight v1.0.0：幂等 ensure 型主机前置。

脚本编排位 = flash 之前的固定步骤（每设备 job 一次，同 host flock 去重）。
不要求 STP_DEVICE_SERIAL——它是 host 级关注。覆盖：全绿零修复、缺包自动
补装、执行位自愈、dialout 补组 relogin 语义、udev 规则写入、check-only
不动手、NFS 指针失败。
"""

from __future__ import annotations

import importlib.util
import json
import os
import time as real_time
from pathlib import Path

import pytest


_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "agent" / "scripts" / "flash_preflight" / "v1.0.0"
)

spec = importlib.util.spec_from_file_location(
    "flash_preflight_v100", _SCRIPT_DIR / "flash_preflight.py"
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


class _CmdStub:
    """路由：dpkg-query 按安装集判定；sudo 记录命令串并可配置结果。"""

    def __init__(self, *, installed_pkgs=("all",), sudo_rc=0,
                 apt_fail=False):
        self.installed = set(installed_pkgs)
        self.sudo_rc = sudo_rc
        self.apt_fail = apt_fail
        self.sudo_cmds: list[str] = []
        self.argvs: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.argvs.append(list(argv))
        if argv[:1] == ["dpkg-query"]:
            pkg = argv[-1]
            if pkg in self.installed or self.installed == {"all"}:
                return _P(stdout="install ok installed")
            return _P(stdout="deinstall ok config-files", returncode=1)
        if argv[:2] == ["sudo", "-n"]:
            cmd = argv[-1] if argv[-2:-1] == ["-c"] else ""
            self.sudo_cmds.append(cmd)
            if "apt-get install" in cmd:
                if self.apt_fail:
                    return _P(stderr="no network", returncode=100)
                # 模拟安装成功：把命令行里的包名翻转为已安装
                parts = cmd.split()
                try:
                    start = parts.index("--no-install-recommends") + 1
                except ValueError:
                    start = parts.index("install") + 2
                self.installed.update(
                    p for p in parts[start:] if not p.startswith("-"))
            return _P()


class _P:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture
def base(tmp_path, monkeypatch):
    """绿基线：NFS 指针在位、flash_tool 在位且可执行、dialout/udev 均 OK。"""
    monkeypatch.setenv("STP_NFS_ROOT", str(tmp_path))
    fw = tmp_path / "firmware" / "MLD"
    fw.mkdir(parents=True)
    (fw / "latest.json").write_text('{"version": "9.9.9.9"}',
                                    encoding="utf-8")
    tool_dir = (tmp_path / "flashtool" /
                "SP_Flash_Tool_Selector_exe_Linux_v1.2444.00.100")
    tool_dir.mkdir(parents=True)
    exe = tool_dir / "flash_tool"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)

    fake_time = _FakeTime()
    monkeypatch.setattr(pf, "time", fake_time)
    monkeypatch.setattr(pf, "_locate_flashtool", lambda: str(exe))
    monkeypatch.setattr(pf, "_user_in_dialout", lambda: True)
    monkeypatch.setattr(pf, "_udev_rule_ok", lambda rd: True)

    stub = _CmdStub()
    # sudo 通道与 subprocess 通道共用同一桩：apt 成功时翻转安装集；
    # sudo 侧需回传与 subprocess_run 相同的 (rc, 合并输出) 二元组
    monkeypatch.setattr(
        pf, "_sudo_sh",
        lambda cmd: (lambda p: (p.returncode,
                                ((p.stdout or "") +
                                 (p.stderr or "")).strip()[-300:])
                     )(stub(["sudo", "-n", "sh", "-c", cmd])))
    monkeypatch.setattr(pf, "subprocess_run",
                        lambda argv, **kw: stub(argv, **kw))
    return {
        "exe": str(exe),
        "stub": stub,
        "nfs_break": lambda: monkeypatch.setenv(
            "STP_NFS_ROOT", str(tmp_path / "missing-nfs")),
    }


def _params(monkeypatch, **params):
    params.pop("locales", None)
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps(params))


def test_all_green_zero_remediation(base, monkeypatch, capsys):
    _params(monkeypatch)
    pf.main()
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["success"] is True
    assert base["stub"].sudo_cmds == ["true"]  # 仅 sudo 探测本身，零修复动作
    checks = {i["check"] for i in payload["metrics"]["items"]}
    assert checks == {"qt-libs", "flashtool", "dialout-group",
                      "udev-rule", "sudo-nopasswd",
                      "nfs-firmware-pointer"}


def test_missing_packages_auto_installed(base, monkeypatch, capsys):
    # 单桩原则：apt 安装成功需反馈到同一 dpkg 视图——清空基线安装集即可
    base["stub"].installed = set()
    _params(monkeypatch)
    pf.main()
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["success"] is True
    install_cmds = [c for c in base["stub"].sudo_cmds
                    if "apt-get install" in c]
    assert install_cmds
    for pkg in ("libice6", "libsm6", "libxrender1",
                "libfontconfig1", "libglib2.0-0"):
        assert pkg in install_cmds[0]


def test_flashtool_exec_bit_self_heals(base, tmp_path, monkeypatch, capsys,
                                       context=None):
    """执行位丢失：chmod 自愈并记 fixed；即便其它项独立失败也不影响断言。"""
    exe_path = tmp_path / "fake_flash_tool"
    exe_path.write_text("x", encoding="utf-8")
    exe_path.chmod(0o644)
    monkeypatch.setattr(pf, "_locate_flashtool", lambda: str(exe_path))
    real_chmod = os.chmod
    chmod_seen: list = []

    def spy_chmod(path, mode):
        chmod_seen.append(str(path))
        return real_chmod(path, mode)

    monkeypatch.setattr(pf.os, "chmod", spy_chmod)
    base["nfs_break"]()
    _params(monkeypatch)
    pf.main()
    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip().splitlines()[-1])
    assert exe_path.stat().st_mode & 0o111  # 真实生效
    assert any(i["check"] == "flashtool" and i["fixed"]
               for i in payload["metrics"]["items"])
    assert payload["success"] is False  # fixture 缺 NFS 指针 → 整体仍败
    # 失败明细只应指向 NFS，不应把已自愈的 flashtool 记为失败
    assert "firmware/MLD/latest.json" in payload["error_message"]
    assert "chmod" not in payload["error_message"]


def test_dialout_fix_records_pending_relogin(base, monkeypatch, capsys):
    monkeypatch.setattr(pf, "_user_in_dialout", lambda: False)
    base["nfs_break"]()
    _params(monkeypatch)
    monkeypatch.setattr(pf.time, "sleep", lambda s: None)
    pf.main()
    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip().splitlines()[-1])
    it = next(i for i in payload["metrics"]["items"]
              if i["check"] == "dialout-group")
    assert it["fixed"] is True
    assert "pending_relogin" in it["detail"]
    assert it["ok"] is True  # usermod 成功即视为已处置，提示重启生效
    assert "dialout-group" not in payload["error_message"]


def test_udev_rule_write_command_shape(base, monkeypatch, capsys):
    monkeypatch.setattr(pf, "_udev_rule_ok", lambda rd: False)
    _params(monkeypatch)
    monkeypatch.setattr(pf.time, "sleep", lambda s: None)
    pf.main()
    cmd = "\n".join(c for c in base["stub"].sudo_cmds
                    if "98-ttyacm-mtk.rules" in c)
    assert "98-ttyacm-mtk.rules" in cmd
    assert 'ATTRS{idVendor}=="0e8d"' in cmd.replace("'", "")
    assert "0666" in cmd
    assert "udevadm control --reload" in cmd


def test_check_only_mode_is_readonly(base, monkeypatch, capsys):
    stub = _CmdStub(installed_pkgs=set())
    monkeypatch.setattr(pf, "subprocess_run",
                        lambda argv, **kw: stub(argv, **kw))
    monkeypatch.setattr(pf, "_locate_flashtool", lambda: None)
    monkeypatch.setattr(pf, "_user_in_dialout", lambda: False)
    monkeypatch.setattr(pf, "_udev_rule_ok", lambda rd: False)
    base["nfs_break"]()
    _params(monkeypatch, fix=False)
    pf.main()
    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip().splitlines()[-1])
    assert payload["success"] is False
    # 只读模式：零修复动作、零 fixed 标记；缺项如实体现在 items
    assert stub.sudo_cmds == []
    assert all(not i["fixed"] for i in payload["metrics"]["items"])
    qt = next(i for i in payload["metrics"]["items"]
              if i["check"] == "qt-libs")
    assert qt["ok"] is False and qt["detail"].startswith("missing:")


def test_nfs_pointer_failure_lists_exact_path(base, monkeypatch, capsys):
    base["nfs_break"]()
    _params(monkeypatch)
    pf.main()
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["success"] is False
    assert "firmware/MLD/latest.json" in payload["error_message"]
