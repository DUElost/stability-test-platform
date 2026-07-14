"""Watcher 子系统启动 configure 冒烟测试（P0 #1）。

验证 main.py 传入的 kwargs 与各单例 configure() 签名一致，避免默认
STP_WATCHER_ENABLED=true 时 Agent 启动 TypeError。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.agent.artifact_uploader import ArtifactUploader
from backend.agent.watcher.emitter import OutboxDrainer
from backend.agent.watcher.manager import LogWatcherManager


@pytest.fixture(autouse=True)
def _reset_singletons():
    LogWatcherManager._reset_for_tests()
    OutboxDrainer._reset_for_tests()
    ArtifactUploader._reset_for_tests()
    yield
    LogWatcherManager._reset_for_tests()
    OutboxDrainer._reset_for_tests()
    ArtifactUploader._reset_for_tests()


def test_log_watcher_manager_configure_accepts_main_kwargs():
    mgr = LogWatcherManager.instance()
    mgr.configure(
        adb=MagicMock(),
        adb_path="adb",
        local_db=MagicMock(),
        sio_client=MagicMock(),
        api_url="http://localhost:8000",
        agent_secret="secret",
        agent_instance_id="agent-inst-001",
        nfs_base_dir="/mnt/nfs",
    )
    assert mgr.is_configured()


def test_artifact_uploader_configure_accepts_main_kwargs():
    uploader = ArtifactUploader.instance()
    uploader.configure(
        api_url="http://localhost:8000",
        agent_secret="secret",
        host_id="42",
        agent_instance_id="agent-inst-001",
    )
    uploader.start()
    uploader.stop(drain=False, timeout=0.5)


def test_outbox_drainer_configure_accepts_main_kwargs():
    drainer = OutboxDrainer.instance()
    drainer.configure(
        local_db=MagicMock(),
        api_url="http://localhost:8000",
        agent_secret="secret",
        interval_seconds=5.0,
        batch_size=50,
    )
    assert drainer.is_configured()
