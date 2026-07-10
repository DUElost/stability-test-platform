"""agent_installer — RunConsole 安装路径单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.services.agent_installer import (
    get_active_install_console_id,
    prepare_install_agent,
    start_install_agent_runconsole,
)


@pytest.fixture
def mock_host():
    host = MagicMock()
    host.id = "host-abc"
    host.ip = "10.0.0.5"
    host.ssh_port = 22
    return host


def test_prepare_install_agent_missing_host():
    with patch("backend.services.agent_installer.SessionLocal") as sl:
        db = MagicMock()
        sl.return_value = db
        db.get.return_value = None
        out = prepare_install_agent("missing")
    assert out["ok"] is False
    assert "not found" in out["message"]


def test_prepare_install_agent_ok(mock_host):
    creds = MagicMock()
    creds.user = "android"
    creds.password = "secret"
    creds.key_path = None

    with (
        patch("backend.services.agent_installer.SessionLocal") as sl,
        patch(
            "backend.services.agent_installer.resolve_host_ssh_credentials",
            return_value=(creds, False),
        ),
    ):
        db = MagicMock()
        sl.return_value = db
        db.get.return_value = mock_host
        out = prepare_install_agent("host-abc")

    assert out["ok"] is True
    assert out["host_id"] == "host-abc"
    assert "ansible-playbook" in out["cmd"][0]
    assert "agent_host_id=host-abc" in out["cmd"][-1]
    out["cleanup"]()


def test_start_install_runconsole_registers_active():
    rc = MagicMock()
    rc.start.return_value = "con-test-1"
    rc.log_file_path.return_value = MagicMock()

    prep = {
        "ok": True,
        "host_id": "host-abc",
        "ip": "10.0.0.5",
        "cmd": ["ansible-playbook", "pb.yml"],
        "env": {},
        "cwd": "/tmp/ansible",
        "cleanup": MagicMock(),
        "cmd_line": "ansible-playbook pb.yml",
    }

    with (
        patch("backend.services.agent_installer.prepare_install_agent", return_value=prep),
        patch("backend.services.agent_installer.RunConsole") as rc_cls,
        patch("builtins.open", MagicMock()),
    ):
        rc_cls.instance.return_value = rc
        out = start_install_agent_runconsole("host-abc", initiated_by="admin")

    assert out["ok"] is True
    assert out["console_run_id"] == "con-test-1"
    assert get_active_install_console_id("host-abc") == "con-test-1"
    prep["cleanup"].assert_not_called()
