# -*- coding: utf-8 -*-
"""flash_preflight v1.0.3（#2284）：ttyACM 规则判据**双形态**。

只认 0660 会让未升级主机（规则仍 0666）被判「规则缺失」→ 调 wrapper 重写 →
若 wrapper 也未升级（仍写 0666），第二轮复查仍失败 → **preflight 卡刷机**。
故两个形态并存一个车队收敛周期。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_V103 = _SCRIPTS / "flash_preflight" / "v1.0.3" / "flash_preflight.py"


def _load():
    spec = importlib.util.spec_from_file_location("flash_preflight_v103", _V103)
    assert spec and spec.loader, f"cannot locate {_V103}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_rule(tmp_path, body: str):
    (tmp_path / "98-ttyacm-mtk.rules").write_text(body, encoding="utf-8")
    return str(tmp_path)


def test_new_form_0660_dialout_is_accepted(tmp_path):
    pf = _load()
    rules = _write_rule(
        tmp_path, 'KERNEL=="ttyACM*", ATTRS{idVendor}=="0e8d", GROUP="dialout", MODE="0660"\n',
    )
    assert pf._udev_rule_ok(rules) is True


def test_legacy_form_0666_is_still_accepted(tmp_path):
    """未升级主机（安装器/playbook 仍是旧形态）不得被判缺失——否则卡刷机。"""
    pf = _load()
    rules = _write_rule(
        tmp_path, 'KERNEL=="ttyACM*", ATTRS{idVendor}=="0e8d", MODE="0666"\n',
    )
    assert pf._udev_rule_ok(rules) is True


def test_0660_without_dialout_is_not_accepted(tmp_path):
    """0660 但组不是 dialout（默认 root:root）：Agent 用户写不了 → 不算可用。"""
    pf = _load()
    rules = _write_rule(
        tmp_path, 'KERNEL=="ttyACM*", ATTRS{idVendor}=="0e8d", MODE="0660"\n',
    )
    assert pf._udev_rule_ok(rules) is False


def test_other_vendor_or_mode_is_not_accepted(tmp_path):
    pf = _load()
    other_vendor = _write_rule(
        tmp_path, 'KERNEL=="ttyACM*", ATTRS{idVendor}=="1234", GROUP="dialout", MODE="0660"\n',
    )
    assert pf._udev_rule_ok(other_vendor) is False

    tmp2 = tmp_path / "other"
    tmp2.mkdir()
    unrelated = _write_rule(
        tmp2, 'KERNEL=="ttyACM*", ATTRS{idVendor}=="0e8d", MODE="0644"\n',
    )
    assert pf._udev_rule_ok(unrelated) is False


def test_missing_rules_dir_is_not_ok(tmp_path):
    pf = _load()
    assert pf._udev_rule_ok(str(tmp_path / "nope")) is False


def test_constants_carry_both_forms():
    pf = _load()
    assert 'GROUP="dialout"' in pf._UDEV_RULE_LINE
    assert 'MODE="0660"' in pf._UDEV_RULE_LINE
    assert 'MODE="0666"' in pf._UDEV_RULE_LINE_LEGACY
    assert pf._UDEV_RULE_PATH == "/etc/udev/rules.d/98-ttyacm-mtk.rules"


@pytest.mark.parametrize("form", ["_UDEV_RULE_LINE", "_UDEV_RULE_LINE_LEGACY"])
def test_both_forms_round_trip_through_checker(form, tmp_path):
    """两个常量自身都必须被判据接受（防止常量与判据再次漂移）。"""
    pf = _load()
    body = getattr(pf, form)
    assert pf._udev_rule_ok(_write_rule(tmp_path, body)) is True
