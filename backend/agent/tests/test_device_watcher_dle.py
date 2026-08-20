"""#310 — inotifyd-only DLE 创建/上送（Reconciler 未接管时的兜底路径）。

覆盖 ``DeviceLogWatcher._maybe_register_device_log_event``：
- pull 成功 → DeviceLogEventClient.create_local_event + EventUploader enqueue；
- pull 失败 → create_pull_failed_event（信号仍落 outbox）；
- reconciler 激活 → inotifyd 路径不注册 DLE（由 reconciler 独占）。

不涉及真实 HTTP/ADB：DeviceLogEventClient / EventUploader / 平台探测均 mock。
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.agent.registry.local_db import LocalDB
from backend.agent.watcher.device_watcher import DeviceLogWatcher
from backend.agent.watcher.policy import WatcherPolicy
from backend.agent.watcher.sources import (
    ProbeResult,
    WatcherCapability,
    WatcherEvent,
)


@pytest.fixture
def db(tmp_path):
    db = LocalDB()
    db.initialize(str(tmp_path / "agent.db"))
    yield db
    db.close()


def _probe_root() -> ProbeResult:
    return ProbeResult(
        capability=WatcherCapability.INOTIFYD_ROOT,
        accessible_categories=["AEE", "VENDOR_AEE"],
        inaccessible_categories={},
        is_root=True,
        reasons=[],
    )


def _watcher(db, **over) -> DeviceLogWatcher:
    kwargs = dict(
        adb_path="adb",
        local_db=db,
        host_id="H1",
        serial="SX",
        job_id=77,
        policy=WatcherPolicy(),
        capability=WatcherCapability.INOTIFYD_ROOT,
        probe_result=_probe_root(),
    )
    kwargs.update(over)
    return DeviceLogWatcher(**kwargs)


def _event(category: str, filename: str = "db.0.0") -> WatcherEvent:
    return WatcherEvent(
        category=category,
        event_mask="n",
        dir_path=f"/data/{category.lower()}",
        filename=filename,
        full_path=f"/data/{category.lower()}/{filename}",
        detected_at=datetime(2026, 4, 19, 10, 0, 0, tzinfo=timezone.utc),
    )


def _patch_env_stack():
    """Mock 外部依赖：DeviceLogEventClient / EventUploader / 平台与 collector。"""
    stack = ExitStack()
    mocks = (
        stack.enter_context(
            patch("backend.agent.aee.device_log_event_client.DeviceLogEventClient")
        ),
        stack.enter_context(patch("backend.agent.event_uploader.EventUploader")),
        stack.enter_context(
            patch(
                "backend.agent.aee.collector.get_collector_for_platform",
                return_value=None,
            )
        ),
        stack.enter_context(
            patch(
                "backend.agent.device_platform.detect_device_platform",
                return_value="MTK",
            )
        ),
    )
    return stack, mocks


def test_inotifyd_only_pull_success_registers_dle_and_enqueues(
    db, tmp_path, monkeypatch,
):
    """Reconciler 未接管：inotifyd pull 成功 → DLE create + EventUploader enqueue。"""
    aee_root = tmp_path / "aee-local"
    event_dir = aee_root / "2026_0803_db.02.NE"
    event_dir.mkdir(parents=True)
    (event_dir / "main.dbg").write_text("x", encoding="utf-8")
    monkeypatch.setenv("STP_AEE_LOCAL_ROOT", str(aee_root))

    stack, (
        mock_client_cls,
        mock_uploader_cls,
        _mock_collector,
        _mock_platform,
    ) = _patch_env_stack()
    with stack:
        client = MagicMock()
        client.create_local_event.return_value = "event-1"
        mock_client_cls.from_env.return_value = client

        uploader = MagicMock()
        mock_uploader_cls.is_enabled.return_value = True
        mock_uploader_cls.instance.return_value = uploader

        w = _watcher(db)  # _aee_reconciler_active 默认 False
        w._on_pull_done(
            _event("AEE"),
            {"artifact_uri": str(event_dir), "size_bytes": 10},
        )
        mock_client_cls.from_env.assert_called_once()
        mock_uploader_cls.is_enabled.assert_called()

    client.create_local_event.assert_called_once()
    kwargs = client.create_local_event.call_args.kwargs
    assert kwargs["local_path"] == event_dir.resolve()
    assert kwargs["job_id"] == 77
    assert kwargs["link_signal_seq_no"] is not None  # outbox 已落信号行
    uploader.enqueue_local_event.assert_called_once()
    assert uploader.enqueue_local_event.call_args.kwargs["event"]["id"] == "event-1"


def test_inotifyd_only_pull_failed_registers_pull_failed_event(db, monkeypatch):
    """pull 失败（空 enrichment）：仍落信号 + DLE pull_failed 记录。"""
    stack, (mock_client_cls, _mock_uploader_cls, *_rest) = _patch_env_stack()
    with stack:
        client = MagicMock()
        client.create_pull_failed_event.return_value = "event-2"
        mock_client_cls.from_env.return_value = client

        w = _watcher(db)
        w._on_pull_done(_event("AEE"), {})

    client.create_pull_failed_event.assert_called_once()
    kwargs = client.create_pull_failed_event.call_args.kwargs
    assert kwargs["job_id"] == 77
    assert kwargs["link_signal_seq_no"] is not None
    assert db.count_pending_log_signals() == 1


def test_reconciler_active_suppresses_inotifyd_dle_registration(db, tmp_path, monkeypatch):
    """reconciler 激活：AEE 由 reconciler 独占，inotifyd 路径不再建 DLE。"""
    aee_root = tmp_path / "aee-local"
    event_dir = aee_root / "2026_0803_db.02.JE"
    event_dir.mkdir(parents=True)
    monkeypatch.setenv("STP_AEE_LOCAL_ROOT", str(aee_root))

    stack, (mock_client_cls, _mock_uploader_cls, *_rest) = _patch_env_stack()
    with stack:
        client = MagicMock()
        mock_client_cls.from_env.return_value = client

        w = _watcher(db)
        w.set_aee_reconciler_active(True)
        w._on_pull_done(
            _event("AEE"),
            {"artifact_uri": str(event_dir), "size_bytes": 10},
        )

    client.create_local_event.assert_not_called()
    client.create_pull_failed_event.assert_not_called()
    assert db.count_pending_log_signals() == 0
