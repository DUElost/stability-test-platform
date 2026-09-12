"""#784: main.py shutdown stop 作用域与启动组对齐（静态契约）。"""

from __future__ import annotations

from pathlib import Path


def _main_source() -> str:
    return (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")


def test_shutdown_stops_archiver_monitor_uploader_outside_watcher_branch():
    src = _main_source()
    # 定位 finally 关停段：EventUploader.stop 必须出现在 log_signal_drainer 分支外
    finally_idx = src.rfind("finally:")
    assert finally_idx > 0
    shutdown = src[finally_idx:]
    eu = shutdown.find("EventUploader.instance().stop")
    arch = shutdown.find("LogArchiver.instance().stop")
    disk = shutdown.find("LocalDiskMonitor.instance().stop")
    drain_branch = shutdown.find("if log_signal_drainer is not None:")
    assert eu > 0 and arch > 0 and disk > 0 and drain_branch > 0
    # 三者均在 watcher/drainer 分支之前
    assert eu < drain_branch
    assert arch < drain_branch
    assert disk < drain_branch


def test_recovery_sync_periodic_loop_present():
    src = _main_source()
    assert "recovery_sync_periodic_started" in src
    assert "STP_RECOVERY_SYNC_INTERVAL_SECONDS" in src
    assert "_recovery_sync_stop" in src
