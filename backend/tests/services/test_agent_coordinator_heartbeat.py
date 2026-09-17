"""#1520 垂直切片：Agent coordinator_heartbeat 服务层直测。"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.services.agent_coordinator_heartbeat import (
    _CoordinatorHeartbeatIn,
    _VALID_COORDINATOR_PHASES,
    record_agent_coordinator_heartbeat,
)


class TestCoordinatorPhases:
    def test_known_phases(self):
        assert "INIT" in _VALID_COORDINATOR_PHASES
        assert "PATROL" in _VALID_COORDINATOR_PHASES
        assert "UNKNOWN" not in _VALID_COORDINATOR_PHASES


class TestCoordinatorHeartbeatGuards:
    @pytest.mark.asyncio
    async def test_empty_payload_accepts(self):
        db = AsyncMock()
        db.get = AsyncMock(return_value=None)
        db.commit = AsyncMock()
        out = await record_agent_coordinator_heartbeat(
            db,
            _CoordinatorHeartbeatIn(
                host_id="h1",
                agent_instance_id="a1",
                plan_run_hosts=[],
                jobs=[],
            ),
        )
        assert out.accepted is True
        assert out.stale_plan_run_host_ids == []
        db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_agent_instance_stale(self):
        host = MagicMock()
        host.last_agent_instance_id = "stored-instance"
        prh = MagicMock()
        prh.coordinator_epoch = 3

        async def _get(model, key):
            name = getattr(model, "__name__", "")
            if name == "Host":
                return host
            return prh

        db = AsyncMock()
        db.get = AsyncMock(side_effect=_get)
        out = await record_agent_coordinator_heartbeat(
            db,
            _CoordinatorHeartbeatIn(
                host_id="h1",
                agent_instance_id="other-instance",
                plan_run_hosts=[{"id": 10, "plan_run_id": 1, "host_id": "h1"}],
                jobs=[],
            ),
        )
        assert out.accepted is False
        assert out.agent_instance_stale is True
        assert out.stale_plan_run_host_ids == [10]
        assert out.current_coordinator_epochs[10] == 3
