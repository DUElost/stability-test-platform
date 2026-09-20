"""#784: Agent shutdown stop 作用域与启动组对齐（静态契约）。

#736 后停机序列在 ``graceful_shutdown.py``；本文件保留 #784 顺序契约，
并断言顺序不因搬家而回潮。
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_shutdown_stops_archiver_monitor_uploader_outside_watcher_branch():
    src = (
        Path(__file__).resolve().parents[1] / "graceful_shutdown.py"
    ).read_text(encoding="utf-8")
    body = src[src.find("def shutdown_agent_runtime") :]
    eu = body.find("EventUploader.instance().stop")
    arch = body.find("LogArchiver.instance().stop")
    disk = body.find("LocalDiskMonitor.instance().stop")
    drain_branch = body.find("if log_signal_drainer is not None:")
    assert eu > 0 and arch > 0 and disk > 0 and drain_branch > 0
    # 三者均在 watcher/drainer 分支之前
    assert eu < drain_branch
    assert arch < drain_branch
    assert disk < drain_branch


def test_recovery_sync_periodic_loop_present():
    import backend.agent.job_runtime as job_runtime
    from tools.dev.source_anchor import SourceGuard

    # 正锚点：job_runtime 启动周期 recovery；否定字面量已迁到 recovery_runtime
    guard = SourceGuard.of_module(job_runtime).anchored("start_periodic_recovery_sync(")
    guard.assert_absent(
        "def _recovery_sync_loop()",
        why="#736 recovery_runtime 已抽出，job_runtime 不得回潮内联周期 loop",
    )
    runtime = (
        Path(__file__).resolve().parents[1] / "recovery_runtime.py"
    ).read_text(encoding="utf-8")
    assert "recovery_sync_periodic_started" in runtime
    assert "STP_RECOVERY_SYNC_INTERVAL_SECONDS" in runtime
    reco = (Path(__file__).resolve().parents[1] / "recovery_executor.py").read_text(
        encoding="utf-8"
    )
    assert "_coerce_recovery_interval" in reco


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, 60.0),
        ("", 60.0),
        ("90", 90.0),
        ("3", 5.0),
        ("-1", 5.0),
        ("abc", 60.0),
        ("nan", 60.0),
        ("inf", 60.0),
        ("-inf", 60.0),
    ],
)
def test_coerce_recovery_interval_guards(raw, expected):
    """#1710：非法/nan/inf 回落默认；负/过小夹到 5。"""
    from backend.agent.recovery_executor import _coerce_recovery_interval

    assert _coerce_recovery_interval(raw) == expected
