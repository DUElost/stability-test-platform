"""#1520 垂直切片：Agent host heartbeat 服务层直测。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.agent_host_heartbeat import (
    HeartbeatRequest,
    record_agent_host_heartbeat,
    suggested_heartbeat_interval,
    suggested_log_rate_limit,
)


class TestSuggestedBackpressure:
    def test_interval_scales_and_clamps(self, monkeypatch):
        import backend.services.agent_host_heartbeat as mod

        monkeypatch.setattr(mod, "HEARTBEAT_INTERVAL_MIN", 15)
        monkeypatch.setattr(mod, "HEARTBEAT_INTERVAL_MAX", 60)
        monkeypatch.setattr(mod, "HEARTBEAT_INTERVAL_BASE", 20)
        assert suggested_heartbeat_interval(0) == 20
        assert suggested_heartbeat_interval(10) == 21
        assert suggested_heartbeat_interval(400) == 60

    def test_log_rate_scales_and_floors(self, monkeypatch):
        import backend.services.agent_host_heartbeat as mod

        monkeypatch.setattr(mod, "LOG_RATE_LIMIT_BASE", 200)
        monkeypatch.setattr(mod, "LOG_RATE_LIMIT_MIN", 20)
        assert suggested_log_rate_limit(0) == 200
        assert suggested_log_rate_limit(25) == 180
        assert suggested_log_rate_limit(1000) == 20


class TestHostHeartbeatGuards:
    @pytest.mark.asyncio
    async def test_creates_host_when_missing(self, monkeypatch):
        db = AsyncMock()
        db.get = AsyncMock(return_value=None)
        db.add = MagicMock()
        db.execute = AsyncMock(
            return_value=MagicMock(
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
            )
        )
        db.commit = AsyncMock()
        monkeypatch.setattr(
            "backend.services.agent_host_heartbeat.compute_script_catalog_version_async",
            AsyncMock(return_value="v1"),
        )
        monkeypatch.setattr(
            "backend.services.agent_version_gate.resolve_agent_min_version",
            lambda: "1.0.0",
        )
        out = await record_agent_host_heartbeat(
            db,
            HeartbeatRequest(host_id="new-host", script_catalog_version="v1"),
        )
        assert out.script_catalog_outdated is False
        assert out.capacity["online_healthy_devices"] == 0
        db.add.assert_called_once()
        db.commit.assert_awaited()
