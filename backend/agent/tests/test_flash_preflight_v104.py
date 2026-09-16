"""flash_preflight v1.0.4：可写性判据与「规则在位」解耦（#2353）。

守什么（**item 级**，不是只测判据函数）：
- `0660` 规则 + 当前进程组集合不含 dialout → `dialout-group` **判否**。
  v1.0.3 在这里假通过：它把宽化后的 `_udev_rule_ok`（两形态都算「在位」）
  当成了「该用户可写」的证据，于是「规则是 0660、用户不是 dialout 成员」
  被放行，刷机在设备侧以 EACCES/STATUS_ERR 失败；
- 「持久成员已补齐但服务未重启」（`/etc/group` 有、进程组集合没有）同样
  判否并给出重启指引——不再 warning 放行；
- `0666` 旧形态仍算放行（warning，提示收敛形态）→ 未升级主机不受影响；
- `udev-rule` 项与 wrapper 修复流语义不变（两形态都算在位）。
"""

from __future__ import annotations

import importlib.util
import json
import time as real_time
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "agent" / "scripts"
_SCRIPT_DIR = _SCRIPTS / "flash_preflight" / "v1.0.4"

spec = importlib.util.spec_from_file_location(
    "flash_preflight_v104", _SCRIPT_DIR / "flash_preflight.py"
)
pf = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(pf)

_RULE_0660 = (
    'KERNEL=="ttyACM*", ATTRS{idVendor}=="0e8d", GROUP="dialout", MODE="0660"\n'
)
_RULE_0660_WITH_REASON = (
    "# MTK ttyACM 0660+dialout：Agent 用户属 dialout 可写（#2284）\n" + _RULE_0660
)
_RULE_0666 = 'KERNEL=="ttyACM*", ATTRS{idVendor}=="0e8d", MODE="0666"\n'


class _FakeTime:
    def sleep(self, seconds):  # pragma: no cover - 测试永不真睡
        pass

    def time(self):
        return real_time.time()

    def monotonic(self):
        return real_time.monotonic()


class _P:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def _rule_dir(tmp_path, content, name="rules"):
    rules = tmp_path / name
    rules.mkdir(exist_ok=True)
    if content is not None:
        (rules / "98-ttyacm-mtk.rules").write_text(content, encoding="utf-8")
    return rules


def _prepare(monkeypatch, tmp_path, *, rule, persistent=False, process=False,
             fix=False):
    def fake_run(argv, **kwargs):
        if argv[:1] == ["dpkg-query"]:
            return _P(stdout="install ok installed", returncode=0)
        return _P()

    monkeypatch.setattr(pf, "subprocess_run", fake_run)
    exe = tmp_path / "flash_tool"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setattr(pf, "_locate_flashtool", lambda: str(exe))
    fw = tmp_path / "firmware" / "MLD"
    fw.mkdir(parents=True, exist_ok=True)
    (fw / "latest.json").write_text('{"version": "9.9.9.9"}', encoding="utf-8")
    monkeypatch.setenv("STP_NFS_ROOT", str(tmp_path))
    monkeypatch.setattr(pf, "time", _FakeTime())
    monkeypatch.setattr(pf, "_user_dialout_persistent", lambda user: persistent)
    monkeypatch.setattr(pf, "_user_in_dialout", lambda: process)
    rules = _rule_dir(tmp_path, rule)
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({
        "fix": fix, "dialout_user": "android",
        "udev_rules_dir": str(rules)}))
    return rules


def _run(capsys):
    pf.main()
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _item(payload, name):
    return next(i for i in payload["metrics"]["items"] if i["check"] == name)


# ── 形态探测：拆出独立入口，未知/无关规则不误判 ────────────────────────────


def test_rule_form_reports_both_forms(tmp_path):
    assert pf._udev_rule_form(str(_rule_dir(tmp_path, _RULE_0660, "a"))) == "0660"
    assert pf._udev_rule_form(
        str(_rule_dir(tmp_path, _RULE_0660_WITH_REASON, "b"))) == "0660"
    assert pf._udev_rule_form(str(_rule_dir(tmp_path, _RULE_0666, "c"))) == "0666"
    assert pf._udev_rule_form(str(_rule_dir(tmp_path, None, "d"))) is None
    assert pf._udev_rule_form(str(tmp_path / "nope")) is None
    # 在位判定仍由形态派生（两形态皆算在位）
    assert pf._udev_rule_ok(str(_rule_dir(tmp_path, _RULE_0660, "e"))) is True


# ── 核心回归：0660 + 进程不在 dialout → 判否（v1.0.3 在此假通过）────────────


def test_0660_without_dialout_process_is_rejected(monkeypatch, capsys, tmp_path):
    _prepare(monkeypatch, tmp_path, rule=_RULE_0660,
             persistent=False, process=False)
    payload = _run(capsys)
    item = _item(payload, "dialout-group")
    assert item["ok"] is False
    assert payload["success"] is False
    # 不得再声称「由 udev 0666 规则放行」
    assert "0666" not in item["detail"]
    assert "0660" in item["detail"]
    assert "provisioning" in item["detail"]
    # 规则本身在位 → udev-rule 项语义不变
    assert _item(payload, "udev-rule")["ok"] is True


def test_0660_persistent_member_without_restart_is_rejected(
        monkeypatch, capsys, tmp_path):
    """usermod 成功但服务未重启：/etc/group 有、进程组集合没有 → 仍写不了。"""
    _prepare(monkeypatch, tmp_path, rule=_RULE_0660,
             persistent=True, process=False)
    payload = _run(capsys)
    item = _item(payload, "dialout-group")
    assert item["ok"] is False
    assert payload["success"] is False
    assert "重启" in item["detail"]


def test_0660_with_dialout_process_is_ok(monkeypatch, capsys, tmp_path):
    _prepare(monkeypatch, tmp_path, rule=_RULE_0660,
             persistent=True, process=True)
    payload = _run(capsys)
    assert _item(payload, "dialout-group")["ok"] is True
    assert payload["success"] is True


def test_0660_process_in_dialout_but_not_persistent_is_ok(
        monkeypatch, capsys, tmp_path):
    _prepare(monkeypatch, tmp_path, rule=_RULE_0660,
             persistent=False, process=True)
    payload = _run(capsys)
    assert _item(payload, "dialout-group")["ok"] is True
    assert "provisioning" in _item(payload, "dialout-group")["detail"]


# ── 兼容面：旧形态 0666 对任何人放行 → 仍算通过（warning 提示收敛）──────────


def test_0666_legacy_without_dialout_still_ok(monkeypatch, capsys, tmp_path):
    _prepare(monkeypatch, tmp_path, rule=_RULE_0666,
             persistent=False, process=False)
    payload = _run(capsys)
    item = _item(payload, "dialout-group")
    assert item["ok"] is True
    assert payload["success"] is True
    assert item["detail"] in payload["metrics"]["warnings"]


def test_no_rule_and_no_dialout_is_rejected(monkeypatch, capsys, tmp_path):
    _prepare(monkeypatch, tmp_path, rule=None, persistent=False, process=False)
    payload = _run(capsys)
    assert _item(payload, "dialout-group")["ok"] is False
    assert _item(payload, "udev-rule")["ok"] is False
    assert payload["success"] is False


# ── 修复流不变：缺规则经 wrapper 写入后按新形态复判 ────────────────────────


def test_udev_repair_writes_new_form_then_rejudges(
        monkeypatch, capsys, tmp_path):
    rules = _prepare(monkeypatch, tmp_path, rule=None,
                     persistent=False, process=False, fix=True)
    monkeypatch.setattr(pf, "_priv_capable", lambda sub: True)
    calls = []

    def fake_priv(sub, *extra):
        calls.append((sub, extra))
        (rules / "98-ttyacm-mtk.rules").write_text(_RULE_0660, encoding="utf-8")
        return 0, ""

    monkeypatch.setattr(pf, "_priv_run", fake_priv)
    payload = _run(capsys)
    assert calls == [("ensure-udev-rule", ())]
    assert _item(payload, "udev-rule")["ok"] is True
    assert _item(payload, "udev-rule")["fixed"] is True
    # 规则补齐了，但进程仍不在 dialout → 可写性判否（这正是 v1.0.3 漏掉的情形）
    assert _item(payload, "dialout-group")["ok"] is False
    assert payload["success"] is False
