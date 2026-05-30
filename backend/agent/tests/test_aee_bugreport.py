"""Regression tests for bugreport export cooldown."""

from __future__ import annotations

import subprocess
from pathlib import Path

from backend.agent.aee import bugreport as br


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
