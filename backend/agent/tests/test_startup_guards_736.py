"""#736: startup gates extracted from main."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.agent.startup_guards import (
    check_agent_version,
    ensure_adb_server_on_startup,
    version_lt,
)


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("1.0.0", "1.0.1", True),
        ("1.0.1", "1.0.0", False),
        ("1.0", "1.0.0", False),
        ("1.0.0", "1.0", False),
        ("2.0.0", "10.0.0", True),
        ("not-a-version", "1.0.0", False),
    ],
)
def test_version_lt(a, b, expected):
    assert version_lt(a, b) is expected


def test_check_agent_version_exits_when_too_old():
    with (
        patch(
            "backend.agent.startup_guards.send_heartbeat",
            return_value={"agent_min_version": "99.0.0"},
        ),
        patch("backend.agent.startup_guards.agent_version", "1.0.0"),
        patch("backend.agent.startup_guards.sys.exit") as exit_mock,
    ):
        check_agent_version("http://x", "h", [], {})
    exit_mock.assert_called_once_with(1)


def test_check_agent_version_skips_on_heartbeat_failure():
    with (
        patch(
            "backend.agent.startup_guards.send_heartbeat",
            side_effect=RuntimeError("down"),
        ),
        patch("backend.agent.startup_guards.sys.exit") as exit_mock,
    ):
        check_agent_version("http://x", "h", [], {})
    exit_mock.assert_not_called()


def test_ensure_adb_server_on_startup_ok():
    with patch("backend.agent.startup_guards.device_discovery") as dd:
        dd.ensure_single_adb_server.return_value = {
            "port": 5037,
            "killed": [],
            "started": True,
            "skipped": False,
        }
        assert ensure_adb_server_on_startup("adb") is True


def test_main_wires_startup_guards():
    import backend.agent.main as agent_main
    from tools.dev.source_anchor import SourceGuard

    guard = SourceGuard.of_module(agent_main).anchored("check_agent_version(")
    guard.assert_absent(
        "agent_version_too_old",
        why="#736 startup_guards 已抽出，main 不得回潮版本门禁字面量",
    )
    guard.assert_absent(
        "adb_server_reconciled",
        why="#736 ADB 收敛已迁出 main",
    )
