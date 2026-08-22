"""HddSpillMonitor 单元测试（ADR-0025 / ADR-0028 Track C — EventUploader-only spill）。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.agent.local_disk_monitor import HddSpillMonitor


@pytest.fixture(autouse=True)
def reset_singleton():
    HddSpillMonitor._reset_for_tests()
    yield
    HddSpillMonitor._reset_for_tests()


def test_below_threshold_no_spill(tmp_path):
    cifs = tmp_path / "cifs"
    cifs.mkdir()
    disk_fn = MagicMock(return_value={"usage_percent": 42.0})
    mon = HddSpillMonitor.instance().configure(
        hdd_root=str(tmp_path),
        cifs_root=str(cifs),
        spill_threshold_pct=80.0,
        disk_usage_fn=disk_fn,
    )

    n = mon.check_once()

    assert n == 0


def test_above_threshold_enqueues_via_event_uploader(tmp_path, monkeypatch):
    """#213 C1: spill only enqueues LOCAL via EventUploader (no rglob copytree)."""
    hdd = tmp_path / "hdd"
    hdd.mkdir()
    cifs = tmp_path / "cifs"
    cifs.mkdir()

    call_count = {"n": 0}

    def _usage(*_a):
        call_count["n"] += 1
        return {"usage_percent": 90.0 if call_count["n"] <= 2 else 50.0}

    mon = HddSpillMonitor.instance().configure(
        hdd_root=str(hdd),
        cifs_root=str(cifs),
        spill_threshold_pct=80.0,
        target_pct=70.0,
        disk_usage_fn=_usage,
        api_url="http://cp",
        agent_secret="sec",
        host_id="host-1",
    )

    event = {
        "id": "evt-1",
        "state": "LOCAL",
        "local_path": str(hdd / "db.01.ANR"),
    }
    client = MagicMock()
    client.list_events.return_value = [event]
    uploader = MagicMock()
    uploader.enqueue_local_event.return_value = True

    with (
        patch(
            "backend.agent.event_uploader.EventUploader.is_enabled",
            return_value=True,
        ),
        patch(
            "backend.agent.event_uploader.EventUploader.instance",
            return_value=uploader,
        ),
        patch(
            "backend.agent.aee.device_log_event_client.DeviceLogEventClient.from_env",
            return_value=client,
        ),
    ):
        n = mon.check_once()

    assert n == 1
    client.list_events.assert_called_with(state="LOCAL", limit=50)
    # #382: 溢出事件必须带 prune_after_upload —— 上送校验后释放本地磁盘，
    # 不依赖默认关闭的 STP_EVENT_UPLOADER_PRUNE_LOCAL。
    uploader.enqueue_local_event.assert_called_once_with(
        event=event, force=True, prune_after_upload=True,
    )
    assert list(cifs.rglob("*")) == []  # no direct copytree into cifs
    assert mon.snapshot_metrics()["spilled_total"] == 1


def test_above_threshold_uploader_disabled_does_not_rglob_copy(tmp_path):
    """#213 C1: no EventUploader → no legacy filesystem spill."""
    hdd = tmp_path / "hdd" / "folder" / "SERIAL" / "aee_exp" / "2026_0601_db.01"
    hdd.mkdir(parents=True)
    (hdd / "__exp_main.txt").write_text("crash", encoding="utf-8")
    cifs = tmp_path / "cifs"
    cifs.mkdir()
    disk_fn = MagicMock(return_value={"usage_percent": 90.0})
    mon = HddSpillMonitor.instance().configure(
        hdd_root=str(tmp_path / "hdd"),
        cifs_root=str(cifs),
        spill_threshold_pct=80.0,
        disk_usage_fn=disk_fn,
    )

    with patch(
        "backend.agent.event_uploader.EventUploader.is_enabled",
        return_value=False,
    ):
        n = mon.check_once()

    assert n == 0
    assert hdd.exists()
    assert list(cifs.rglob("__exp_main.txt")) == []
    assert mon.snapshot_metrics()["spilled_total"] == 0


def test_ssd_mode_skips_spill(tmp_path, monkeypatch):
    """#213 C2: SSD fallback root disables spill entirely."""
    hdd = tmp_path / "ssd"
    hdd.mkdir()
    cifs = tmp_path / "cifs"
    cifs.mkdir()
    monkeypatch.setenv("STP_AEE_SSD_FALLBACK_ROOT", str(hdd))
    disk_fn = MagicMock(return_value={"usage_percent": 99.0})
    mon = HddSpillMonitor.instance().configure(
        hdd_root=str(hdd),
        cifs_root=str(cifs),
        spill_threshold_pct=80.0,
        disk_usage_fn=disk_fn,
    )

    with patch(
        "backend.agent.event_uploader.EventUploader.is_enabled",
        return_value=True,
    ) as enabled:
        n = mon.check_once()

    assert n == 0
    enabled.assert_not_called()
    disk_fn.assert_not_called()


def test_no_local_events_just_warns(tmp_path):
    hdd = tmp_path / "hdd"
    hdd.mkdir()
    cifs = tmp_path / "cifs"
    cifs.mkdir()
    disk_fn = MagicMock(return_value={"usage_percent": 90.0})
    mon = HddSpillMonitor.instance().configure(
        hdd_root=str(hdd),
        cifs_root=str(cifs),
        spill_threshold_pct=80.0,
        disk_usage_fn=disk_fn,
        api_url="http://cp",
        agent_secret="sec",
        host_id="host-1",
    )
    client = MagicMock()
    client.list_events.return_value = []

    with (
        patch(
            "backend.agent.event_uploader.EventUploader.is_enabled",
            return_value=True,
        ),
        patch(
            "backend.agent.aee.device_log_event_client.DeviceLogEventClient.from_env",
            return_value=client,
        ),
    ):
        n = mon.check_once()

    assert n == 0


def test_usage_read_failure_skips_spill(tmp_path):
    cifs = tmp_path / "cifs"
    cifs.mkdir()
    disk_fn = MagicMock(side_effect=OSError("permission denied"))
    mon = HddSpillMonitor.instance().configure(
        hdd_root=str(tmp_path),
        cifs_root=str(cifs),
        spill_threshold_pct=80.0,
        disk_usage_fn=disk_fn,
    )

    n = mon.check_once()

    assert n == 0
    metrics = mon.snapshot_metrics()
    assert metrics["local_disk_usage_pct"] is None


@pytest.mark.parametrize(
    "disk_info",
    [
        {"total_gb": None, "used_gb": None, "free_gb": None, "usage_percent": None},
        {"total_gb": 0, "used_gb": 0, "free_gb": 0},
        {"usage_percent": "n/a"},
        {"usage_percent": float("nan")},
        {"usage_percent": float("inf")},
        {"usage_percent": -1},
        {"usage_percent": 101},
        "not-a-dict",
    ],
)
def test_unusable_usage_percent_skips_spill(tmp_path, disk_info):
    """get_disk_usage 失败形态 / 缺 key / 非法值都不得当成 0% 健康而跳过 spill。"""
    cifs = tmp_path / "cifs"
    cifs.mkdir()
    disk_fn = MagicMock(return_value=disk_info)
    mon = HddSpillMonitor.instance().configure(
        hdd_root=str(tmp_path),
        cifs_root=str(cifs),
        spill_threshold_pct=80.0,
        disk_usage_fn=disk_fn,
    )

    n = mon.check_once()

    assert n == 0
    assert mon.snapshot_metrics()["local_disk_usage_pct"] is None
