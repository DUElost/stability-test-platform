"""最新 flash_firmware 的 ttyACM 可写性判据分叉守卫（#2353；#2048 教训）。

v1.3.16 及以前只认 `0666` 文本（`_udev_has_mtk_0666_rule`），带来两个方向的错判：

- 在已升 `0660 + GROUP="dialout"` 的宿主上误报「no udev MODE=0666 rule」（只影响
  WARNING 级，但诊断信息是错的）；
- 反过来，若把「规则在位」当作「当前进程可写」，`0660` 规则 + 进程不在 dialout
  （**写不了串口**）这一状态会被放行 → 刷机中途 EACCES/`STATUS_ERR`。

本守卫**动态解析最新版本目录**（钉历史版本目录的用例在新版本出现后即失效），钉住三条语义：

- `0660` 规则 + 进程不在 dialout → `ttyacm-write-path` **判否**且指引含补组/重启；
- `0660` 规则 + 进程在 dialout → 通过；
- `0666` 规则 + 进程不在 dialout → 通过（兼容未升级主机）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

FIRMWARE_DIR = (
    Path(__file__).resolve().parents[2] / "agent" / "scripts" / "flash_firmware"
)
_RULE_0660 = (
    'KERNEL=="ttyACM*", ATTRS{idVendor}=="0e8d", GROUP="dialout", MODE="0660"\n'
)
_RULE_0666 = 'KERNEL=="ttyACM*", ATTRS{idVendor}=="0e8d", MODE="0666"\n'


def _version_key(name: str) -> tuple:
    return tuple(int(part) for part in name[1:].split("."))


def _latest_entry() -> Path:
    versions = [p for p in FIRMWARE_DIR.iterdir()
                if p.is_dir() and p.name.startswith("v")]
    latest = max(versions, key=lambda p: _version_key(p.name))
    entries = [p for p in latest.glob("*.py") if not p.name.startswith("_")]
    assert entries, f"flash_firmware {latest.name} 无入口文件"
    return entries[0]


def _load_latest():
    entry = _latest_entry()
    spec = importlib.util.spec_from_file_location(
        f"flash_firmware_latest_{entry.parent.name}", entry
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return entry.parent.name, module


def _tty_item(monkeypatch, tmp_path, *, form, in_dialout):
    version, mod = _load_latest()
    exe = tmp_path / "flash_tool"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setattr(mod, "_ldd_missing_libs", lambda path, env: [])
    monkeypatch.setattr(mod, "_ttyacm_writable_now", lambda: None)
    monkeypatch.setattr(mod, "_user_in_dialout", lambda: in_dialout)
    monkeypatch.setattr(mod, "_udev_rule_form", lambda rules_dir=None: form)
    _ok, report = mod._precheck_environment(
        str(exe), "adb", False, False
    )
    item = next(i for i in report["items"] if i["check"] == "ttyacm-write-path")
    return version, item


def test_rule_form_parses_both_forms(tmp_path):
    _version, mod = _load_latest()
    for name, content, want in (
        ("a", _RULE_0660, "0660"),
        ("b", _RULE_0666, "0666"),
        ("c", None, None),
    ):
        d = tmp_path / name
        d.mkdir()
        if content:
            (d / "98-ttyacm-mtk.rules").write_text(content, encoding="utf-8")
        assert mod._udev_rule_form(str(d)) == want, name
    assert mod._udev_rule_form(str(tmp_path / "nope")) is None


def test_latest_0660_without_dialout_is_rejected(monkeypatch, tmp_path):
    version, item = _tty_item(monkeypatch, tmp_path, form="0660", in_dialout=False)
    assert item["ok"] is False, (
        f"{version}：0660 规则 + 进程不在 dialout 被判通过 —— 该进程写不了串口（#2353）"
    )
    assert "dialout" in item["detail"] and "restart" in item["detail"]


def test_latest_0660_with_dialout_is_ok(monkeypatch, tmp_path):
    _version, item = _tty_item(monkeypatch, tmp_path, form="0660", in_dialout=True)
    assert item["ok"] is True


def test_latest_0666_legacy_is_ok(monkeypatch, tmp_path):
    _version, item = _tty_item(monkeypatch, tmp_path, form="0666", in_dialout=False)
    assert item["ok"] is True, "0666 对任何本地用户放行，未升级主机不得被判否"


def test_latest_no_rule_without_dialout_is_rejected(monkeypatch, tmp_path):
    _version, item = _tty_item(monkeypatch, tmp_path, form=None, in_dialout=False)
    assert item["ok"] is False
    assert "usermod" in item["detail"]
