"""DeviceLogWatcher → ArtifactUploader 集成（ADR-0018 5B2 Task #5）。

目标：验证 _on_pull_done 在 AEE/VENDOR_AEE 且 pull 成功时，会触发
ArtifactUploader.submit；其它路径（ANR/MOBILELOG、pull 失败、oversized）不触发。

不涉及真实 HTTP —— ArtifactUploader 被替换为 MagicMock 单例。
"""

from __future__ import annotations

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


# ----------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    db = LocalDB()
    db.initialize(str(tmp_path / "agent.db"))
    yield db
    db.close()


def _probe_root() -> ProbeResult:
    return ProbeResult(
        capability=WatcherCapability.INOTIFYD_ROOT,
        accessible_categories=["ANR", "AEE", "VENDOR_AEE"],
        inaccessible_categories={},
        is_root=True,
        reasons=[],
    )


def _watcher(db, **over):
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


# ----------------------------------------------------------------------
# 1. AEE pull 成功 → submit 1 次
# ----------------------------------------------------------------------

@patch("backend.agent.artifact_uploader.ArtifactUploader.instance")
def test_aee_success_submits_to_uploader(mock_instance, db):
    uploader = MagicMock()
    mock_instance.return_value = uploader

    w = _watcher(db)
    event = _event("AEE")
    enrichment = {
        "artifact_uri": "/mnt/nfs/jobs/77/AEE/1700000000_db.0.0",
        "sha256": "a" * 64,
        "size_bytes": 2048,
        "first_lines": "first few\nlines",
    }
    w._on_pull_done(event, enrichment)

    assert uploader.submit.call_count == 1
    kwargs = uploader.submit.call_args.kwargs
    assert kwargs["job_id"] == 77
    assert kwargs["artifact_type"] == "aee_crash"
    assert kwargs["storage_uri"] == enrichment["artifact_uri"]
    assert kwargs["size_bytes"] == 2048
    assert kwargs["checksum"] == "a" * 64
    assert kwargs["source_category"] == "AEE"
    assert kwargs["source_path_on_device"] == event.full_path


@patch("backend.agent.artifact_uploader.ArtifactUploader.instance")
def test_vendor_aee_success_submits_with_correct_type(mock_instance, db):
    uploader = MagicMock()
    mock_instance.return_value = uploader
    w = _watcher(db)
    w._on_pull_done(
        _event("VENDOR_AEE"),
        {"artifact_uri": "/mnt/nfs/x", "sha256": "b" * 64, "size_bytes": 1},
    )
    assert uploader.submit.call_args.kwargs["artifact_type"] == "vendor_aee_crash"


# ----------------------------------------------------------------------
# 2. pull 失败（空 enrichment）→ 不 submit
# ----------------------------------------------------------------------

@patch("backend.agent.artifact_uploader.ArtifactUploader.instance")
def test_pull_failure_does_not_submit(mock_instance, db):
    uploader = MagicMock()
    mock_instance.return_value = uploader

    w = _watcher(db)
    w._on_pull_done(_event("AEE"), {})   # puller 失败 → 空 enrichment

    uploader.submit.assert_not_called()


# ----------------------------------------------------------------------
# 3. 超大文件（artifact_uri=None）→ 不 submit
# ----------------------------------------------------------------------

@patch("backend.agent.artifact_uploader.ArtifactUploader.instance")
def test_oversized_file_does_not_submit(mock_instance, db):
    uploader = MagicMock()
    mock_instance.return_value = uploader

    w = _watcher(db)
    w._on_pull_done(
        _event("AEE"),
        {"artifact_uri": None, "sha256": None,
         "size_bytes": 9999999999, "first_lines": None},
    )

    uploader.submit.assert_not_called()


# ----------------------------------------------------------------------
# 4. ANR / MOBILELOG 类别 → 即便 artifact_uri 存在（不应出现，兜底）也不 submit
# ----------------------------------------------------------------------

@patch("backend.agent.artifact_uploader.ArtifactUploader.instance")
@pytest.mark.parametrize("category", ["ANR", "MOBILELOG"])
def test_non_whitelisted_category_does_not_submit(mock_instance, db, category):
    uploader = MagicMock()
    mock_instance.return_value = uploader
    w = _watcher(db)
    w._on_pull_done(
        _event(category),
        {"artifact_uri": "/mnt/nfs/x", "sha256": "c" * 64, "size_bytes": 1},
    )
    uploader.submit.assert_not_called()


# ----------------------------------------------------------------------
# 5. Uploader 异常不冒泡（解耦不变量）
# ----------------------------------------------------------------------

@patch("backend.agent.artifact_uploader.ArtifactUploader.instance")
def test_uploader_exception_is_swallowed(mock_instance, db):
    uploader = MagicMock()
    uploader.submit.side_effect = RuntimeError("boom")
    mock_instance.return_value = uploader

    w = _watcher(db)
    # 正常 emit 已由 _safe_emit 吞，uploader 异常也必须不上冒
    w._on_pull_done(
        _event("AEE"),
        {"artifact_uri": "/mnt/nfs/x", "sha256": "d" * 64, "size_bytes": 1},
    )
    # _safe_emit 也跑了 → outbox 里有记录
    assert len(db.get_pending_log_signals()) == 1
