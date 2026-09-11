# -*- coding: utf-8 -*-
"""monkey_test v1.2.1 stdout 纯 JSON 契约（#808）。

引擎对脚本 stdout 整份 ``json.loads``（pipeline_engine.py:1504-1510），任何
非 JSON 行都会让 payload 解析失败、metrics 静默丢失（rc=0 仍报成功）。本测试
验证：资源推送日志走 stderr、stdout 只含最终 JSON，并锁定 ``resource_push``
metrics 留痕（目录缺失不再静默 no-op）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str, rel_path: str):
    path = _SCRIPTS / rel_path
    sys.path.insert(0, str(path.parent))
    try:
        sys.modules.pop("_adb", None)
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader, f"cannot locate {path}"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.modules.pop("_adb", None)
        sys.path.remove(str(path.parent))


def _prepare(monkeypatch, mod, tmp_path, *, with_resource_dir: bool):
    aimonkey_dir = tmp_path / "AIMonkeyTest_test"
    aimonkey_dir.mkdir()
    if with_resource_dir:
        resource_dir = aimonkey_dir / "resource"
        resource_dir.mkdir()
        (resource_dir / "media.mp4").write_bytes(b"x")

    pushed: list[str] = []
    monkeypatch.setattr(mod, "device_serial", lambda: "SERIAL")
    monkeypatch.setattr(
        mod,
        "params",
        lambda: {"aimonkey_dir": str(aimonkey_dir), "push_resources": True},
    )
    monkeypatch.setattr(
        mod, "_resolve_aimonkey_dir", lambda cfg: Path(cfg["aimonkey_dir"])
    )
    monkeypatch.setattr(mod, "_shell", lambda serial, cmd, timeout=30: (0, "Pixel 7"))

    def fake_push(serial, local, remote, timeout=60):
        pushed.append(remote)
        return True

    monkeypatch.setattr(mod, "_push_file", fake_push)
    return aimonkey_dir, pushed


def test_monkey_test_stdout_is_valid_json_with_resource_push(monkeypatch, tmp_path, capsys):
    mod = _load("monkey_test_v121_stdout", "monkey_test/v1.2.1/monkey_test.py")
    _prepare(monkeypatch, mod, tmp_path, with_resource_dir=True)

    mod.main()

    captured = capsys.readouterr()
    payload = json.loads(captured.out)  # stdout 必须是合法 JSON（不容日志行）
    assert payload["success"] is True
    assert payload["metrics"]["resource_push"] == "pushed:1"
    assert "[monkey_test]" in captured.err  # 推送日志走 stderr


def test_monkey_test_records_missing_resource_dir(monkeypatch, tmp_path, capsys):
    mod = _load("monkey_test_v121_missing", "monkey_test/v1.2.1/monkey_test.py")
    _prepare(monkeypatch, mod, tmp_path, with_resource_dir=False)

    mod.main()

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["success"] is True
    assert payload["metrics"]["resource_push"] == "missing"
