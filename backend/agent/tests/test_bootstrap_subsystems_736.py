"""#736: disk/watcher bootstrap extracted from main."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.agent.bootstrap_subsystems import start_disk_and_watcher_subsystems


def _settings():
    s = MagicMock()
    s.stp_log_archive_interval_seconds = 30
    s.stp_log_archive_grace_seconds = 5
    s.stp_local_disk_monitor_interval_seconds = 60
    s.stp_local_disk_spill_threshold = 90
    s.stp_local_disk_spill_target = 70
    return s


def test_start_disk_subsystems_skips_watcher_when_disabled():
    archiver = MagicMock()
    archiver.configure.return_value = archiver
    event_uploader = MagicMock()
    event_uploader.configure.return_value = event_uploader
    disk_monitor = MagicMock()
    disk_monitor.configure.return_value = disk_monitor

    with (
        patch(
            "backend.agent.bootstrap_subsystems.watcher_subsystem_enabled",
            return_value=False,
        ),
        patch(
            "backend.agent.bootstrap_subsystems.get_aee_local_root",
            return_value="/tmp/hdd",
        ),
        patch(
            "backend.agent.bootstrap_subsystems.resolve_shared_storage_root",
            return_value="/tmp/cifs",
        ),
        patch(
            "backend.agent.bootstrap_subsystems.get_disk_archive_settings",
            return_value=_settings(),
        ),
        patch(
            "backend.agent.bootstrap_subsystems.LogArchiver.instance",
            return_value=archiver,
        ),
        patch("backend.agent.bootstrap_subsystems.ScanRunner.instance") as scan,
        patch("backend.agent.bootstrap_subsystems.UnisocScanRunner.instance") as uscan,
        patch("backend.agent.bootstrap_subsystems.UploadManager.instance") as upload,
        patch(
            "backend.agent.bootstrap_subsystems.EventUploader.instance",
            return_value=event_uploader,
        ),
        patch(
            "backend.agent.bootstrap_subsystems.LocalDiskMonitor.instance",
            return_value=disk_monitor,
        ),
        patch("backend.agent.bootstrap_subsystems.LogWatcherManager.instance") as watcher,
        patch("backend.agent.bootstrap_subsystems.OutboxDrainer.instance") as drainer,
        patch("backend.agent.bootstrap_subsystems.ArtifactUploader.instance") as art,
    ):
        result = start_disk_and_watcher_subsystems(
            local_db=MagicMock(),
            api_url="http://x",
            agent_secret="s",
            host_id="h1",
            agent_instance_id="inst",
            adb=MagicMock(),
            adb_path="adb",
            sio_client=MagicMock(),
        )

    assert result is None
    archiver.configure.assert_called_once()
    archiver.start.assert_called_once()
    scan.return_value.configure.assert_called_once_with()
    uscan.return_value.configure.assert_called_once_with()
    upload.return_value.configure.assert_called_once_with()
    event_uploader.start.assert_called_once()
    disk_monitor.configure.assert_called_once()
    disk_monitor.start.assert_called_once()
    watcher.assert_not_called()
    drainer.assert_not_called()
    art.assert_not_called()


def test_start_disk_subsystems_enables_watcher_stack():
    archiver = MagicMock()
    archiver.configure.return_value = archiver
    event_uploader = MagicMock()
    event_uploader.configure.return_value = event_uploader
    disk_monitor = MagicMock()
    disk_monitor.configure.return_value = disk_monitor
    watcher_mgr = MagicMock()
    watcher_mgr.reconcile_on_startup.return_value = 2
    drainer = MagicMock()
    Outbox = MagicMock()
    Outbox.configure.return_value = drainer
    art = MagicMock()
    art.configure.return_value = art

    with (
        patch(
            "backend.agent.bootstrap_subsystems.watcher_subsystem_enabled",
            return_value=True,
        ),
        patch(
            "backend.agent.bootstrap_subsystems.get_aee_local_root",
            return_value="/tmp/hdd",
        ),
        patch(
            "backend.agent.bootstrap_subsystems.resolve_shared_storage_root",
            return_value="",
        ),
        patch(
            "backend.agent.bootstrap_subsystems.get_disk_archive_settings",
            return_value=_settings(),
        ),
        patch(
            "backend.agent.bootstrap_subsystems.LogArchiver.instance",
            return_value=archiver,
        ),
        patch("backend.agent.bootstrap_subsystems.ScanRunner.instance"),
        patch("backend.agent.bootstrap_subsystems.UnisocScanRunner.instance"),
        patch("backend.agent.bootstrap_subsystems.UploadManager.instance"),
        patch(
            "backend.agent.bootstrap_subsystems.EventUploader.instance",
            return_value=event_uploader,
        ),
        patch(
            "backend.agent.bootstrap_subsystems.LocalDiskMonitor.instance",
            return_value=disk_monitor,
        ),
        patch(
            "backend.agent.bootstrap_subsystems.LogWatcherManager.instance",
            return_value=watcher_mgr,
        ),
        patch(
            "backend.agent.bootstrap_subsystems.OutboxDrainer.instance",
            return_value=Outbox,
        ),
        patch(
            "backend.agent.bootstrap_subsystems.ArtifactUploader.instance",
            return_value=art,
        ),
    ):
        result = start_disk_and_watcher_subsystems(
            local_db=MagicMock(),
            api_url="http://x",
            agent_secret="s",
            host_id="h1",
            agent_instance_id="inst",
            adb=MagicMock(),
            adb_path="adb",
            sio_client=MagicMock(),
        )

    assert result is drainer
    disk_monitor.configure.assert_not_called()  # empty cifs_root skips spill monitor
    watcher_mgr.configure.assert_called_once()
    watcher_mgr.reconcile_on_startup.assert_called_once()
    drainer.start.assert_called_once()
    art.start.assert_called_once()


def test_main_wires_bootstrap_helper():
    import backend.agent.main as agent_main
    from tools.dev.source_anchor import SourceGuard

    # 正锚点：bootstrap 调用仍在 main；否定：不得回潮直连 LogArchiver.configure
    guard = SourceGuard.of_module(agent_main).anchored(
        "start_disk_and_watcher_subsystems("
    )
    guard.assert_absent(
        "LogArchiver.instance().configure(",
        why="#736 disk/watcher bootstrap 已抽出，main 不得回潮直连 LogArchiver 配置",
    )
