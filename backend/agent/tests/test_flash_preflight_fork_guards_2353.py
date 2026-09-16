"""最新 flash_preflight 的「可写性」分叉守卫（#2353；#2048 教训）。

背景：`_udev_rule_ok` 的语义在 #2284 由「一行含 0666」放宽为「0660+dialout 或
0666 **在位**」。v1.0.3 因此把「规则在位」误当成「该用户可写」——`dialout-group`
项在「0660 规则 + 用户不在 dialout」时假通过（#2353），刷机在设备侧以
EACCES/STATUS_ERR 失败。

既有 item 级用例钉的是**具体版本目录**（v1.0.2 / v1.0.4），新版本一出现就无人
覆盖（#2048 教训）。本守卫**动态解析最新版本目录**，在 item 级钉住三条语义：

- 0660 规则 + 进程组集合不含 dialout → `dialout-group` 判否；
- 0660 规则 + 进程组集合含 dialout → 通过；
- 0666 规则 + 进程组集合不含 dialout → 通过（兼容未升级主机）。

任何新版本把这三条改回去或改错，本文件即红。
"""

from __future__ import annotations

import importlib.util
import json
import time as real_time
from pathlib import Path

PREFLIGHT_DIR = Path(__file__).resolve().parents[2] / "agent" / "scripts" / "flash_preflight"

_RULE_0660 = (
    'KERNEL=="ttyACM*", ATTRS{idVendor}=="0e8d", GROUP="dialout", MODE="0660"\n'
)
_RULE_0666 = 'KERNEL=="ttyACM*", ATTRS{idVendor}=="0e8d", MODE="0666"\n'


def _version_key(name: str) -> tuple:
    return tuple(int(part) for part in name[1:].split("."))


def _latest_preflight_entry() -> Path:
    """最新 preflight 版本入口（数字段排序——字典序会把 v1.0.9 排在 v1.0.10 后）。"""
    versions = [p for p in PREFLIGHT_DIR.iterdir()
                if p.is_dir() and p.name.startswith("v")]
    latest = max(versions, key=lambda p: _version_key(p.name))
    entries = [p for p in latest.glob("*.py") if not p.name.startswith("_")]
    assert entries, f"flash_preflight {latest.name} 无入口文件"
    return entries[0]


def _load_latest():
    entry = _latest_preflight_entry()
    spec = importlib.util.spec_from_file_location(
        f"flash_preflight_latest_{entry.parent.name}", entry
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return entry.parent.name, module


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


def _run_item(monkeypatch, tmp_path, capsys, *, rule, process):
    version, mod = _load_latest()

    def fake_run(argv, **kwargs):
        if argv[:1] == ["dpkg-query"]:
            return _P(stdout="install ok installed", returncode=0)
        return _P()

    monkeypatch.setattr(mod, "subprocess_run", fake_run)
    exe = tmp_path / "flash_tool"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setattr(mod, "_locate_flashtool", lambda: str(exe))
    fw = tmp_path / "firmware" / "MLD"
    fw.mkdir(parents=True, exist_ok=True)
    (fw / "latest.json").write_text('{"version": "9.9.9.9"}', encoding="utf-8")
    monkeypatch.setenv("STP_NFS_ROOT", str(tmp_path))
    monkeypatch.setattr(mod, "time", _FakeTime())
    monkeypatch.setattr(mod, "_user_in_dialout", lambda: process)
    monkeypatch.setattr(mod, "_user_dialout_persistent", lambda user: process)
    rules = tmp_path / "rules"
    rules.mkdir(exist_ok=True)
    (rules / "98-ttyacm-mtk.rules").write_text(rule, encoding="utf-8")
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({
        "fix": False, "dialout_user": "android",
        "udev_rules_dir": str(rules)}))

    mod.main()
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    item = next(i for i in payload["metrics"]["items"]
                if i["check"] == "dialout-group")
    return version, item, payload


def test_latest_0660_without_dialout_is_rejected(monkeypatch, capsys, tmp_path):
    version, item, payload = _run_item(monkeypatch, tmp_path, capsys,
                                       rule=_RULE_0660, process=False)
    assert item["ok"] is False, (
        f"{version}：0660 规则 + 进程不在 dialout 被判通过 —— 可写性假通过（#2353）"
    )
    assert payload["success"] is False
    assert "0666" not in item["detail"], f"{version}：不得再声称 0666 规则放行"


def test_latest_0660_with_dialout_is_ok(monkeypatch, capsys, tmp_path):
    _version, item, _payload = _run_item(monkeypatch, tmp_path, capsys,
                                         rule=_RULE_0660, process=True)
    assert item["ok"] is True


def test_latest_0666_legacy_without_dialout_is_ok(monkeypatch, capsys, tmp_path):
    _version, item, payload = _run_item(monkeypatch, tmp_path, capsys,
                                        rule=_RULE_0666, process=False)
    assert item["ok"] is True, "旧形态 0666 对任何本地用户放行，未升级主机不得被判否"
    assert payload["success"] is True
