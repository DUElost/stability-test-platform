"""#1520 垂直切片：Agent patrol_heartbeat 服务层直测。"""

from __future__ import annotations

import pytest
from backend.services.errors import ServiceError
from unittest.mock import AsyncMock, MagicMock, patch

from backend.models.enums import JobStatus
from backend.services.agent_patrol_heartbeat import (
    PatrolHeartbeatIn,
    record_agent_patrol_heartbeat,
)


def _payload(**overrides):
    base = dict(
        fencing_token="tok",
        cycle_index=1,
        success_delta=0,
        failed_delta=0,
    )
    base.update(overrides)
    return PatrolHeartbeatIn(**base)


class TestPatrolHeartbeatGuards:
    @pytest.mark.asyncio
    async def test_job_not_found_404(self):
        db = AsyncMock()
        db.get = AsyncMock(return_value=None)
        with pytest.raises(ServiceError) as exc:
            await record_agent_patrol_heartbeat(db, 1, _payload())
        assert exc.value.status == 404

    @pytest.mark.asyncio
    async def test_not_running_409(self):
        job = MagicMock()
        job.status = JobStatus.UNKNOWN.value
        db = AsyncMock()
        db.get = AsyncMock(return_value=job)
        with pytest.raises(ServiceError) as exc:
            await record_agent_patrol_heartbeat(db, 1, _payload())
        assert exc.value.status == 409
        assert exc.value.detail["code"] == "JOB_NOT_RUNNING"

    @pytest.mark.asyncio
    async def test_negative_delta_400(self):
        job = MagicMock()
        job.status = JobStatus.RUNNING.value
        db = AsyncMock()
        db.get = AsyncMock(return_value=job)
        with patch(
            "backend.services.agent_patrol_heartbeat._get_valid_runtime_lease",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ):
            with pytest.raises(ServiceError) as exc:
                await record_agent_patrol_heartbeat(
                    db, 1, _payload(success_delta=-1),
                )
        assert exc.value.status == 400
