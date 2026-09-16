"""flash_preflight v1.0.2：运行期 root 面收敛（ADR-0037 D5 / #2133）。

守什么：
- **不再存在 `sudo -n sh -c` 任意命令面**（源码级 + 符号级断言）；
- udev 缺规则 → 只经 wrapper `ensure-udev-rule`（能力探针；缺失时明确失败
  并给出 update_agent.yml 指引）；
- apt 缺包 → 明确失败 + 指引（运行期不装包，装包归 provisioning）；
  skip_apt=true 降级 warning；
- dialout 改读**持久成员**（消除 usermod 假 fixed），并按 进程组 / udev 规则
  逐级降级；两者皆无才失败；
- `sudo-nopasswd` 硬门移除，改为 warning-only 的 `priv-face` 可观测项；
- udev 规则常量与 wrapper 同源（逐字相等）。
"""

from __future__ import annotations

import importlib.util
import json
import time as real_time
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "agent" / "scripts"
_SCRIPT_DIR = _SCRIPTS / "flash_preflight" / "v1.0.2"
_WRAPPER = Path(__file__).resolve().parents[3] / "backend" / "agent" / "stp_agent_priv.py"

spec = importlib.util.spec_from_file_location(
    "flash_preflight_v102", _SCRIPT_DIR / "flash_preflight.py"
)
pf = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(pf)


class _FakeTime:
    def sleep(self, seconds):  # pragma: no cover - 测试永不真睡
        pass

    def time(self):
        return real_time.time()

    def monotonic(self):
        return real_time.monotonic()


class _P:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class _Recorder:
    """记录 subprocess_run 的全部 argv；dpkg 按开关回放。"""

    def __init__(self, packages_installed: bool = True):
        self.packages_installed = packages_installed
        self.calls: "list[list[str]]" = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if argv[:1] == ["dpkg-query"]:
            ok = self.packages_installed
            return _P(stdout=("install ok installed" if ok
                              else "deinstall ok config-files"),
                      returncode=0 if ok else 1)
        return _P()

    def sudo_calls(self) -> "list[list[str]]":
        return [c for c in self.calls if c[:1] == ["sudo"]]


def _prepare_env(monkeypatch, tmp_path, *, packages_installed=True):
    rec = _Recorder(packages_installed=packages_installed)
    monkeypatch.setattr(pf, "subprocess_run", rec)
    exe = tmp_path / "flash_tool"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setattr(pf, "_locate_flashtool", lambda: str(exe))
    fw = tmp_path / "firmware" / "MLD"
    fw.mkdir(parents=True, exist_ok=True)
    (fw / "latest.json").write_text('{"version": "9.9.9.9"}',
                                    encoding="utf-8")
    monkeypatch.setenv("STP_NFS_ROOT", str(tmp_path))
    monkeypatch.setattr(pf, "time", _FakeTime())
    return rec


def _run(capsys):
    pf.main()
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _item(payload, name):
    return next(i for i in payload["metrics"]["items"] if i["check"] == name)


def _set_dialout(monkeypatch, *, persistent, process):
    monkeypatch.setattr(pf, "_user_dialout_persistent", lambda user: persistent)
    monkeypatch.setattr(pf, "_user_in_dialout", lambda: process)


# ── 源码/符号级：不存在任意命令面 ───────────────────────────────────────────


def test_no_shell_command_face_in_source_or_symbols():
    src = (_SCRIPT_DIR / "flash_preflight.py").read_text(encoding="utf-8")
    assert "_sudo_sh" not in src.split('"""', 2)[2]        # 跳过模块 docstring
    assert '"sh", "-c"' not in src
    assert not hasattr(pf, "_sudo_sh")
    # 所有 sudo 调用都必须指向 wrapper 常量
    assert pf._PRIV_WRAPPER == "/usr/local/sbin/stp-agent-priv"


def test_udev_constants_match_wrapper():
    wspec = importlib.util.spec_from_file_location("stp_priv_v102", _WRAPPER)
    w = importlib.util.module_from_spec(wspec)
    assert wspec.loader is not None
    wspec.loader.exec_module(w)
    if not hasattr(w, "UDEV_RULE_PATH"):
        pytest.skip("wrapper 窄面常量未在当前基线（依赖 #2142 合入）")
    assert pf._UDEV_RULE_PATH == w.UDEV_RULE_PATH
    # 本文件钉的是**历史版本** v1.0.2：它的规则文本是旧形态（0666），也就是 wrapper
    # 的 legacy 形态（#2284 起 wrapper 按本机 dialout 组在 0660/0666 间二选一）。
    # 「wrapper ↔ 最新版本」的同源校验在 tests/test_flash_provisioning_prereqs_2133.py。
    assert pf._UDEV_RULE_LINE == w.UDEV_RULE_LINE_LEGACY


# ── udev：只经 wrapper 窄面修复 ─────────────────────────────────────────────


def test_udev_repair_goes_through_wrapper(monkeypatch, capsys, tmp_path):
    rec = _prepare_env(monkeypatch, tmp_path)
    state = {"ok": False}
    priv_calls: "list[tuple]" = []
    monkeypatch.setattr(pf, "_udev_rule_ok", lambda rd: state["ok"])
    monkeypatch.setattr(pf, "_priv_capable", lambda sub: True)

    def fake_priv(sub, *extra):
        priv_calls.append((sub, extra))
        state["ok"] = True
        return 0, ""

    monkeypatch.setattr(pf, "_priv_run", fake_priv)
    _set_dialout(monkeypatch, persistent=True, process=True)

    payload = _run(capsys)
    assert priv_calls == [("ensure-udev-rule", ())]
    assert _item(payload, "udev-rule")["ok"] is True
    assert _item(payload, "udev-rule")["fixed"] is True
    assert payload["success"] is True
    assert {"name": "ensure-udev-rule", "rc": 0} in payload["metrics"]["commands"]
    assert rec.sudo_calls() == []


def test_udev_repair_skipped_when_capability_missing(monkeypatch, capsys, tmp_path):
    _prepare_env(monkeypatch, tmp_path)
    monkeypatch.setattr(pf, "_udev_rule_ok", lambda rd: False)
    monkeypatch.setattr(pf, "_priv_capable", lambda sub: False)
    _set_dialout(monkeypatch, persistent=True, process=True)

    payload = _run(capsys)
    item = _item(payload, "udev-rule")
    assert item["ok"] is False
    assert "update_agent.yml" in item["detail"]
    assert payload["success"] is False


def test_udev_repair_respects_fix_false(monkeypatch, capsys, tmp_path):
    _prepare_env(monkeypatch, tmp_path)
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({"fix": False}))
    monkeypatch.setattr(pf, "_udev_rule_ok", lambda rd: False)
    called = []
    monkeypatch.setattr(pf, "_priv_run",
                        lambda sub, *extra: (called.append(sub), (0, ""))[1])
    _set_dialout(monkeypatch, persistent=True, process=True)

    payload = _run(capsys)
    assert called == []
    assert _item(payload, "udev-rule")["ok"] is False


# ── qt-libs：只检不装 ──────────────────────────────────────────────────────


def test_qt_missing_fails_without_runtime_install(monkeypatch, capsys, tmp_path):
    rec = _prepare_env(monkeypatch, tmp_path, packages_installed=False)
    monkeypatch.setattr(pf, "_udev_rule_ok", lambda rd: True)
    monkeypatch.setattr(pf, "_priv_capable", lambda sub: True)
    _set_dialout(monkeypatch, persistent=True, process=True)

    payload = _run(capsys)
    item = _item(payload, "qt-libs")
    assert item["ok"] is False
    assert "不再安装" in item["detail"]
    assert payload["success"] is False
    # 任何 apt / 任意命令都不允许出现在调用序列里
    flat = " ".join(" ".join(c) for c in rec.calls)
    assert "apt-get" not in flat
    assert rec.sudo_calls() == []


def test_qt_missing_with_skip_apt_is_warning(monkeypatch, capsys, tmp_path):
    _prepare_env(monkeypatch, tmp_path, packages_installed=False)
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({"skip_apt": True}))
    monkeypatch.setattr(pf, "_udev_rule_ok", lambda rd: True)
    monkeypatch.setattr(pf, "_priv_capable", lambda sub: True)
    _set_dialout(monkeypatch, persistent=True, process=True)

    payload = _run(capsys)
    assert _item(payload, "qt-libs")["ok"] is True
    assert any("skip_apt" in w for w in payload["metrics"]["warnings"])
    assert payload["success"] is True


# ── dialout：持久成员判定 + 降级链 ─────────────────────────────────────────


def test_dialout_persistent_only_warns_pending_relogin(monkeypatch, capsys, tmp_path):
    _prepare_env(monkeypatch, tmp_path)
    monkeypatch.setattr(pf, "_udev_rule_ok", lambda rd: True)
    monkeypatch.setattr(pf, "_priv_capable", lambda sub: True)
    _set_dialout(monkeypatch, persistent=True, process=False)

    payload = _run(capsys)
    item = _item(payload, "dialout-group")
    assert item["ok"] is True
    assert "pending_relogin" in item["detail"]
    assert any("pending_relogin" in w for w in payload["metrics"]["warnings"])
    assert payload["success"] is True


def test_dialout_both_present_is_clean(monkeypatch, capsys, tmp_path):
    _prepare_env(monkeypatch, tmp_path)
    monkeypatch.setattr(pf, "_udev_rule_ok", lambda rd: True)
    monkeypatch.setattr(pf, "_priv_capable", lambda sub: True)
    _set_dialout(monkeypatch, persistent=True, process=True)

    payload = _run(capsys)
    assert _item(payload, "dialout-group")["detail"] == ""
    assert not any("dialout" in w for w in payload["metrics"]["warnings"])


def test_dialout_absent_degrades_to_udev_rule(monkeypatch, capsys, tmp_path):
    _prepare_env(monkeypatch, tmp_path)
    monkeypatch.setattr(pf, "_udev_rule_ok", lambda rd: True)
    monkeypatch.setattr(pf, "_priv_capable", lambda sub: True)
    _set_dialout(monkeypatch, persistent=False, process=False)

    payload = _run(capsys)
    item = _item(payload, "dialout-group")
    assert item["ok"] is True
    assert "udev 0666" in item["detail"]


def test_dialout_absent_and_no_rule_fails(monkeypatch, capsys, tmp_path):
    _prepare_env(monkeypatch, tmp_path)
    monkeypatch.setattr(pf, "_udev_rule_ok", lambda rd: False)
    monkeypatch.setattr(pf, "_priv_capable", lambda sub: False)
    _set_dialout(monkeypatch, persistent=False, process=False)

    payload = _run(capsys)
    assert _item(payload, "dialout-group")["ok"] is False
    assert _item(payload, "udev-rule")["ok"] is False
    assert payload["success"] is False


# ── priv-face：warning-only 可观测项 ───────────────────────────────────────


def test_priv_face_missing_wrapper_is_warning_only(monkeypatch, capsys, tmp_path):
    _prepare_env(monkeypatch, tmp_path)
    monkeypatch.setattr(pf, "_udev_rule_ok", lambda rd: True)
    monkeypatch.setattr(pf, "_priv_wrapper_present", lambda: False)
    _set_dialout(monkeypatch, persistent=True, process=True)

    payload = _run(capsys)
    item = _item(payload, "priv-face")
    assert item["ok"] is True
    assert "update_agent.yml" in item["detail"]
    assert any("stp-agent-priv" in w for w in payload["metrics"]["warnings"])
    assert payload["success"] is True          # warning 不判失败


def test_priv_face_old_wrapper_reports_upgrade(monkeypatch, capsys, tmp_path):
    _prepare_env(monkeypatch, tmp_path)
    monkeypatch.setattr(pf, "_udev_rule_ok", lambda rd: True)
    monkeypatch.setattr(pf, "_priv_wrapper_present", lambda: True)
    monkeypatch.setattr(pf, "_priv_capable", lambda sub: False)
    _set_dialout(monkeypatch, persistent=True, process=True)

    payload = _run(capsys)
    item = _item(payload, "priv-face")
    assert item["ok"] is True
    assert "旧版本" in item["detail"]
    assert payload["success"] is True


def test_priv_face_ok_is_silent(monkeypatch, capsys, tmp_path):
    _prepare_env(monkeypatch, tmp_path)
    monkeypatch.setattr(pf, "_udev_rule_ok", lambda rd: True)
    monkeypatch.setattr(pf, "_priv_wrapper_present", lambda: True)
    monkeypatch.setattr(pf, "_priv_capable", lambda sub: True)
    _set_dialout(monkeypatch, persistent=True, process=True)

    payload = _run(capsys)
    assert _item(payload, "priv-face")["detail"] == "wrapper=ok; ensure-udev-rule=ok"
    assert payload["metrics"]["warnings"] == []


def test_sudo_nopasswd_item_is_gone(monkeypatch, capsys, tmp_path):
    _prepare_env(monkeypatch, tmp_path)
    monkeypatch.setattr(pf, "_udev_rule_ok", lambda rd: True)
    monkeypatch.setattr(pf, "_priv_capable", lambda sub: True)
    _set_dialout(monkeypatch, persistent=True, process=True)

    payload = _run(capsys)
    names = {i["check"] for i in payload["metrics"]["items"]}
    assert "sudo-nopasswd" not in names
    assert names == {"qt-libs", "flashtool", "udev-rule", "dialout-group",
                     "priv-face", "nfs-firmware-pointer"}


def test_priv_capable_probe_uses_real_subcommand_argv(monkeypatch):
    """探针必须带子命令本体（#2011：纯 --help 探针抓不到真调用失败）。"""
    seen: "list[list[str]]" = []

    def fake_run(argv, **kwargs):
        seen.append(list(argv))
        return _P(returncode=0)

    monkeypatch.setattr(pf, "subprocess_run", fake_run)
    assert pf._priv_capable("ensure-udev-rule") is True
    assert seen == [["sudo", "-n", pf._PRIV_WRAPPER,
                     "ensure-udev-rule", "--help"]]


@pytest.mark.parametrize("rc", [1, 2])
def test_priv_capable_false_on_nonzero(monkeypatch, rc):
    monkeypatch.setattr(pf, "subprocess_run",
                        lambda argv, **kwargs: _P(returncode=rc))
    assert pf._priv_capable("ensure-udev-rule") is False
