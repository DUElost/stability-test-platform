"""Unit tests for precheck sync helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.models.host import Host
from backend.services.host_updater import _AGENT_SOURCE_DIR
from backend.services.precheck.sync import (
    nfs_path_to_local,
    push_mismatched_scripts,
    sync_host_via_hot_update,
)


def test_nfs_path_to_local_maps_agent_prefix():
    rel = "scripts/check_device/v1.0.0/check_device.py"
    nfs = f"/opt/stability-test-agent/agent/{rel}"
    assert nfs_path_to_local(nfs) == str(_AGENT_SOURCE_DIR / rel)


def test_nfs_path_to_local_rejects_foreign_prefix():
    assert nfs_path_to_local("/mnt/nfs/scripts/foo.py") is None


def test_sync_host_via_hot_update_missing_host(db_session):
    ok, err = sync_host_via_hot_update("missing-host", db_session)
    assert ok is False
    assert err == "host_not_found"


def test_sync_host_via_hot_update_delegates_to_execute_hot_update(db_session):
    host = Host(id="h-1", hostname="agent1", ip="10.0.0.9", ssh_port=22)
    db_session.add(host)
    db_session.commit()

    with patch(
        "backend.services.precheck.sync.resolve_host_ssh_credentials",
        return_value=(MagicMock(user="u", password="p", key_path="", known_hosts_path=""), False),
    ), patch(
        "backend.services.precheck.sync.execute_hot_update",
        return_value={"ok": True},
    ) as hot_update:
        ok, err = sync_host_via_hot_update("h-1", db_session)

    assert ok is True
    assert err is None
    hot_update.assert_called_once()


def test_push_mismatched_scripts_requires_credentials(db_session):
    host = Host(id="h-2", hostname="agent2", ip="10.0.0.10", ssh_port=22)
    db_session.add(host)
    db_session.commit()

    with patch(
        "backend.services.precheck.sync.resolve_host_ssh_credentials",
        return_value=(MagicMock(user="u", password="", key_path="", known_hosts_path=""), False),
    ):
        ok, err = push_mismatched_scripts(
            "h-2",
            [{"name": "check_device", "nfs_path": "/opt/stability-test-agent/agent/x.py"}],
            db_session,
        )

    assert ok is False
    assert err == "no_ssh_credentials"
