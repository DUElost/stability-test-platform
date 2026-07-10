"""Host agent_installed 推导（与 ONLINE/OFFLINE 正交）。"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from backend.api.routes.hosts import _derive_agent_installed, _host_to_out


def test_derive_not_installed():
    h = SimpleNamespace(extra={}, last_heartbeat=None)
    assert _derive_agent_installed(h) == (False, None)


def test_derive_from_flag():
    h = SimpleNamespace(
        extra={"agent_installed": True, "agent_installed_at": "2026-07-10T01:00:00+00:00"},
        last_heartbeat=None,
    )
    ok, at = _derive_agent_installed(h)
    assert ok is True
    assert at == "2026-07-10T01:00:00+00:00"


def test_derive_from_heartbeat_history():
    ts = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)
    h = SimpleNamespace(extra={}, last_heartbeat=ts)
    ok, at = _derive_agent_installed(h)
    assert ok is True
    assert at == ts.isoformat()


def test_host_to_out_exposes_agent_installed():
    host = MagicMock()
    host.id = "h1"
    host.name = "n"
    host.ip = "1.2.3.4"
    host.ssh_port = 22
    host.ssh_user = "android"
    host.ssh_auth_type = "password"
    host.status = "OFFLINE"
    host.watcher_admin_active = True
    host.last_heartbeat = datetime(2026, 7, 10, tzinfo=timezone.utc)
    host.extra = {"agent_installed": True}
    host.mount_status = {}

    out = _host_to_out(host)
    assert out.agent_installed is True
