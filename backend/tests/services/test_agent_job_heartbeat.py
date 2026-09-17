"""#1520 垂直切片：Agent job_heartbeat / extend_lock 服务层直测。"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock, MagicMock, patch

from backend.models.enums import JobStatus
from backend.services.agent_job_heartbeat import (
    ExtendLockIn,
    JobHeartbeatIn,
    extend_agent_job_lock,
    record_agent_job_heartbeat,
)


class TestJobHeartbeatGuards:
    @pytest.mark.asyncio
    async def test_job_not_found_404(self):
        db = AsyncMock()
        db.get = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc:
            await record_agent_job_heartbeat(
                db, 1, JobHeartbeatIn(fencing_token="tok"),
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_invalid_token_409(self):
        job = MagicMock()
        job.status = JobStatus.RUNNING.value
        db = AsyncMock()
        db.get = AsyncMock(return_value=job)
        with patch(
            "backend.services.agent_job_heartbeat._get_valid_runtime_lease",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(HTTPException) as exc:
                await record_agent_job_heartbeat(
                    db, 1, JobHeartbeatIn(fencing_token="bad"),
                )
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_terminal_status_requires_complete(self):
        job = MagicMock()
        job.status = JobStatus.RUNNING.value
        job.started_at = MagicMock()
        db = AsyncMock()
        db.get = AsyncMock(return_value=job)
        db.commit = AsyncMock()
        with patch(
            "backend.services.agent_job_heartbeat._get_valid_runtime_lease",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ):
            with pytest.raises(HTTPException) as exc:
                await record_agent_job_heartbeat(
                    db, 1, JobHeartbeatIn(status="FAILED", fencing_token="tok"),
                )
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "TERMINAL_STATUS_REQUIRES_COMPLETE"

    @pytest.mark.asyncio
    async def test_running_refreshes_updated_at(self):
        job = MagicMock()
        job.status = JobStatus.RUNNING.value
        job.started_at = MagicMock()
        job.updated_at = None
        db = AsyncMock()
        db.get = AsyncMock(return_value=job)
        db.commit = AsyncMock()
        with patch(
            "backend.services.agent_job_heartbeat._get_valid_runtime_lease",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ):
            out = await record_agent_job_heartbeat(
                db, 7, JobHeartbeatIn(fencing_token="tok"),
            )
        assert out == {"job_id": 7, "status": job.status}
        assert job.updated_at is not None
        db.commit.assert_awaited_once()


class TestExtendJobLockGuards:
    @pytest.mark.asyncio
    async def test_job_not_found_404(self):
        result = MagicMock()
        result.scalars.return_value.first.return_value = None
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result)
        with pytest.raises(HTTPException) as exc:
            await extend_agent_job_lock(
                db, 1, ExtendLockIn(fencing_token="tok"),
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_device_not_found_404(self):
        job = MagicMock()
        job.device_id = 9
        result = MagicMock()
        result.scalars.return_value.first.return_value = job
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result)
        db.get = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc:
            await extend_agent_job_lock(
                db, 1, ExtendLockIn(fencing_token="tok"),
            )
        assert exc.value.status_code == 404
        assert "device" in exc.value.detail

    @pytest.mark.asyncio
    async def test_renew_conflict_409(self):
        job = MagicMock()
        job.device_id = 9
        result = MagicMock()
        result.scalars.return_value.first.return_value = job
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result)
        db.get = AsyncMock(return_value=MagicMock())
        with patch(
            "backend.services.agent_job_heartbeat._get_valid_runtime_lease",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ), patch(
            "backend.services.agent_job_heartbeat.extend_lease",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with pytest.raises(HTTPException) as exc:
                await extend_agent_job_lock(
                    db, 1, ExtendLockIn(fencing_token="tok"),
                )
        assert exc.value.status_code == 409
