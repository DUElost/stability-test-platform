"""#736: startup identity / HOST_ID bootstrap extracted from main."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.agent.startup_identity import (
    AgentProcessIdentity,
    bootstrap_process_identity,
    resolve_monitored_mount_points,
    resolve_or_register_host_id,
)


def test_resolve_host_id_uses_configured_host():
    with patch(
        "backend.agent.startup_identity.load_required_host_id",
        return_value="198-51-100-6",
    ) as load, patch(
        "backend.agent.startup_identity.get_registration_settings"
    ) as reg, patch(
        "backend.agent.startup_identity.auto_register_host"
    ) as auto:
        host_id = resolve_or_register_host_id("http://x", {"ip": "198.51.100.6"})

    assert host_id == "198-51-100-6"
    load.assert_called_once()
    reg.assert_not_called()
    auto.assert_not_called()


def test_resolve_host_id_auto_registers_when_enabled():
    settings = MagicMock()
    settings.auto_register_enabled = True
    settings.auto_register_max_retries = 3
    settings.auto_register_retry_delay = 0

    with patch(
        "backend.agent.startup_identity.load_required_host_id",
        side_effect=ValueError("bad"),
    ), patch(
        "backend.agent.startup_identity.get_registration_settings",
        return_value=settings,
    ), patch(
        "backend.agent.startup_identity.auto_register_host",
        side_effect=[RuntimeError("once"), "host-new"],
    ) as auto, patch("backend.agent.startup_identity.time.sleep") as sleep:
        host_id = resolve_or_register_host_id("http://x", {"ip": "1.2.3.4"})

    assert host_id == "host-new"
    assert auto.call_count == 2
    sleep.assert_called_once()


def test_resolve_host_id_exits_when_auto_register_disabled():
    settings = MagicMock()
    settings.auto_register_enabled = False

    with patch(
        "backend.agent.startup_identity.load_required_host_id",
        side_effect=ValueError("bad"),
    ), patch(
        "backend.agent.startup_identity.get_registration_settings",
        return_value=settings,
    ):
        with pytest.raises(SystemExit) as ei:
            resolve_or_register_host_id("http://x", {"ip": "1.2.3.4"})
    assert ei.value.code == 2


def test_bootstrap_process_identity_assembles_fields():
    with patch(
        "backend.agent.startup_identity.get_host_info",
        return_value={"ip": "10.0.0.1"},
    ), patch(
        "backend.agent.startup_identity.generate_agent_instance_id",
        return_value="inst-1",
    ), patch(
        "backend.agent.startup_identity.read_boot_id",
        return_value="boot-1",
    ), patch(
        "backend.agent.startup_identity.read_agent_code_revision",
        return_value="rev",
    ), patch(
        "backend.agent.startup_identity.read_artifact_digest",
        return_value="digest",
    ), patch(
        "backend.agent.startup_identity.resolve_or_register_host_id",
        return_value="host-1",
    ), patch.dict(
        "os.environ",
        {
            "POLL_INTERVAL": "7",
            "MOUNT_POINTS": "a,b",
            "STP_AEE_NFS_ROOT": "",
            "ADB_PATH": "/usr/bin/adb",
            "AGENT_SECRET": "sec",
        },
        clear=False,
    ):
        identity = bootstrap_process_identity("http://api")

    assert isinstance(identity, AgentProcessIdentity)
    assert identity.host_id == "host-1"
    assert identity.agent_instance_id == "inst-1"
    assert identity.boot_id == "boot-1"
    assert identity.poll_interval == 7.0
    assert identity.mount_points == ["a", "b"]
    assert identity.adb_path == "/usr/bin/adb"
    assert identity.agent_secret == "sec"
    assert identity.agent_artifact_digest == "digest"


def test_resolve_monitored_mount_points_includes_shared_storage_root(monkeypatch):
    """#4077: STP_AEE_NFS_ROOT must be monitored even when MOUNT_POINTS is stale."""
    monkeypatch.setenv("MOUNT_POINTS", "/mnt/stp-aee,/mnt/stp-log/")
    monkeypatch.setenv("STP_AEE_NFS_ROOT", "/mnt/stp-log")
    assert resolve_monitored_mount_points() == ["/mnt/stp-aee", "/mnt/stp-log/"]

    monkeypatch.setenv("MOUNT_POINTS", "/mnt/stp-aee")
    monkeypatch.setenv("STP_AEE_NFS_ROOT", "/mnt/stp-log")
    assert resolve_monitored_mount_points() == ["/mnt/stp-aee", "/mnt/stp-log"]

    monkeypatch.delenv("MOUNT_POINTS", raising=False)
    monkeypatch.setenv("STP_AEE_NFS_ROOT", "/mnt/stp-log")
    assert resolve_monitored_mount_points() == ["/mnt/stp-log"]


def test_resolve_monitored_mount_points_preserves_trailing_space_distinct_paths(monkeypatch):
    """#4077 R2: 路径尾部空格是真实路径差异，不得与无空格 STP_AEE_NFS_ROOT 错误归并。"""
    monkeypatch.setenv("MOUNT_POINTS", "/mnt/review-new ")
    monkeypatch.setenv("STP_AEE_NFS_ROOT", "/mnt/review-new")
    assert resolve_monitored_mount_points() == ["/mnt/review-new ", "/mnt/review-new"]


def test_resolve_monitored_mount_points_preserves_legacy_when_shared_root_unset(monkeypatch):
    """#4077 B2: 未设或空值 STP_AEE_NFS_ROOT 时严格保留旧 split 语义（不 trim / 不去重）。"""
    monkeypatch.setenv("MOUNT_POINTS", "/mnt/a , /mnt/a , ")
    monkeypatch.delenv("STP_AEE_NFS_ROOT", raising=False)
    assert resolve_monitored_mount_points() == ["/mnt/a ", " /mnt/a ", " "]

    monkeypatch.setenv("STP_AEE_NFS_ROOT", "")
    assert resolve_monitored_mount_points() == ["/mnt/a ", " /mnt/a ", " "]


def test_bootstrap_process_identity_includes_shared_storage_root():
    """#4077 入口接线：bootstrap_process_identity() 透出合并后的挂载列表。"""
    with patch(
        "backend.agent.startup_identity.get_host_info",
        return_value={"ip": "10.0.0.1"},
    ), patch(
        "backend.agent.startup_identity.generate_agent_instance_id",
        return_value="inst-1",
    ), patch(
        "backend.agent.startup_identity.read_boot_id",
        return_value="boot-1",
    ), patch(
        "backend.agent.startup_identity.read_agent_code_revision",
        return_value="rev",
    ), patch(
        "backend.agent.startup_identity.read_artifact_digest",
        return_value="digest",
    ), patch(
        "backend.agent.startup_identity.resolve_or_register_host_id",
        return_value="host-1",
    ), patch.dict(
        "os.environ",
        {
            "MOUNT_POINTS": "/mnt/stp-aee",
            "STP_AEE_NFS_ROOT": "/mnt/stp-log",
            "AGENT_SECRET": "sec",
        },
        clear=False,
    ):
        identity = bootstrap_process_identity("http://api")

    assert identity.mount_points == ["/mnt/stp-aee", "/mnt/stp-log"]


def test_main_wires_startup_identity():
    import backend.agent.agent_application as app
    from tools.dev.source_anchor import SourceGuard

    # 正锚点：startup identity bootstrap 在编排层；否定：不得回潮直连 load_required_host_id
    guard = SourceGuard.of_module(app).anchored(
        "bootstrap_process_identity("
    )
    guard.assert_absent(
        "load_required_host_id()",
        why="#736 HOST_ID bootstrap 已抽出，编排层不得回潮直连 load_required_host_id",
    )
