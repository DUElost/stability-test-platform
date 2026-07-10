"""Host agent_installed 推导（与 ONLINE/OFFLINE 正交）。"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

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
    host = SimpleNamespace(
        id="h1",
        name="n",
        ip="1.2.3.4",
        ssh_port=22,
        ssh_user="android",
        ssh_auth_type="password",
        status="OFFLINE",
        watcher_admin_active=True,
        last_heartbeat=datetime(2026, 7, 10, tzinfo=timezone.utc),
        extra={"agent_installed": True},
        mount_status={},
    )

    out = _host_to_out(host)
    assert out.agent_installed is True
