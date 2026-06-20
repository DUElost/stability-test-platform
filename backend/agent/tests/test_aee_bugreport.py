"""export_bugreport_for_timestamp 落盘子目录测试 (C-2 / D3)。

覆盖：
    - 默认布局 → bugreport 落盘到 `correlated_bugreports/`(对齐 monolith)
    - 逃生口 STP_WATCHER_AEE_SUBDIR_LAYOUT=stp → 回退 `bugreport/`
    - 与 mobilelog 共用同一 paths.resolve_*_subdir 逃生口
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.agent.aee import bugreport as br


def _fake_run_writes_zip(argv, **kwargs):
    """伪造 adb bugreport：把内容写到 argv[-1](.partial 临时文件),返回 rc=0。"""
    temp_path = argv[-1]
    Path(temp_path).parent.mkdir(parents=True, exist_ok=True)
    Path(temp_path).write_bytes(b"PK\x03\x04 fake-bugreport-zip")
    return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")


def _export(tmp_path) -> bool:
    return br.export_bugreport_for_timestamp(
        serial="SERIAL1",
        timestamp_str="2026-05-28 10:00:00.000",
        output_dir=tmp_path,
        event_type="CRASH",
        cooldown_seconds=0,   # 绕过冷却,纯测落盘目录名
    )


def test_bugreport_default_subdir_is_correlated_bugreports(tmp_path, monkeypatch):
    monkeypatch.delenv("STP_WATCHER_AEE_SUBDIR_LAYOUT", raising=False)
    monkeypatch.setattr(br.subprocess, "run", _fake_run_writes_zip)

    assert _export(tmp_path) is True

    subdirs = [p.name for p in tmp_path.iterdir() if p.is_dir()]
    assert subdirs == ["correlated_bugreports"], f"默认应落盘 correlated_bugreports/,实际 {subdirs}"
    files = list((tmp_path / "correlated_bugreports").iterdir())
    assert len(files) == 1
    assert files[0].name.endswith("_bugreport.zip")


def test_bugreport_stp_layout_falls_back_to_bugreport(tmp_path, monkeypatch):
    monkeypatch.setenv("STP_WATCHER_AEE_SUBDIR_LAYOUT", "stp")
    monkeypatch.setattr(br.subprocess, "run", _fake_run_writes_zip)

    assert _export(tmp_path) is True

    subdirs = [p.name for p in tmp_path.iterdir() if p.is_dir()]
    assert subdirs == ["bugreport"], f"stp 逃生口应回退 bugreport/,实际 {subdirs}"


def test_bugreport_subdir_shares_escape_hatch_with_mobilelog(monkeypatch):
    """C-2: bugreport 与 mobilelog 共用 paths.resolve_*_subdir 逃生口语义。"""
    from backend.agent.aee.paths import resolve_bugreport_subdir, resolve_mobilelog_subdir

    monkeypatch.delenv("STP_WATCHER_AEE_SUBDIR_LAYOUT", raising=False)
    assert resolve_bugreport_subdir() == "bugreport"
    assert resolve_mobilelog_subdir() == "mobilelog"

    monkeypatch.setenv("STP_WATCHER_AEE_SUBDIR_LAYOUT", "correlated")
    assert resolve_bugreport_subdir() == "correlated_bugreports"
    assert resolve_mobilelog_subdir() == "correlated_mobilelogs"


def test_bugreport_failed_attempt_does_not_poison_cooldown(tmp_path, monkeypatch):
    """失败的 bugreport 导出不应写入 cooldown,下一次应允许立即重试。"""
    calls = {"n": 0}

    def _fake_run_fail_then_succeed(argv, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return subprocess.CompletedProcess(
                args=argv, returncode=1, stdout="", stderr="boom",
            )
        temp_path = Path(argv[-1])
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_bytes(b"PK\x03\x04 fake-bugreport-zip")
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="", stderr="",
        )

    monkeypatch.setattr(br.subprocess, "run", _fake_run_fail_then_succeed)
    br._last_export_ts.clear()

    first = br.export_bugreport_for_timestamp(
        serial="SERIAL1",
        timestamp_str="2026-05-28 10:00:00.000",
        output_dir=tmp_path,
        event_type="CRASH",
        cooldown_seconds=300,
    )
    second = br.export_bugreport_for_timestamp(
        serial="SERIAL1",
        timestamp_str="2026-05-28 10:00:00.000",
        output_dir=tmp_path,
        event_type="CRASH",
        cooldown_seconds=300,
    )

    assert first is False
    assert second is True, "失败导出不应占用 cooldown,第二次应允许立即重试"
    assert calls["n"] == 2, "第二次必须真正再次执行 adb bugreport"
