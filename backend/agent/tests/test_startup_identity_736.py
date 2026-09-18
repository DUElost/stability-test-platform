"""#736: startup identity / HOST_ID bootstrap extracted from main."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.agent.startup_identity import (
    AgentProcessIdentity,
    bootstrap_process_identity,
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


def test_main_wires_startup_identity():
    from pathlib import Path

    import backend.agent.main as agent_main

    text = Path(agent_main.__file__).read_text(encoding="utf-8")
    assert "bootstrap_process_identity(" in text
    assert "load_required_host_id()" not in text
