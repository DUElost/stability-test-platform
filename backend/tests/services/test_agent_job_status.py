"""#1520 垂直切片：Agent job_status 服务层直测。"""

from __future__ import annotations

import pytest
from backend.services.errors import ServiceError
from unittest.mock import AsyncMock, MagicMock, patch

from backend.models.enums import JobStatus
from backend.services.agent_job_status import (
    JobStatusUpdate,
    update_agent_job_status,
)


class TestUpdateJobStatusGuards:
    @pytest.mark.asyncio
    async def test_job_not_found_404(self):
        db = AsyncMock()
        db.get = AsyncMock(return_value=None)
        with pytest.raises(ServiceError) as exc:
            await update_agent_job_status(
                db, 1, JobStatusUpdate(status="RUNNING", fencing_token="tok"),
            )
        assert exc.value.status == 404

    @pytest.mark.asyncio
    async def test_unknown_status_400(self):
        job = MagicMock()
        db = AsyncMock()
        db.get = AsyncMock(return_value=job)
        with patch(
            "backend.services.agent_job_status.require_valid_runtime_lease",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ):
            with pytest.raises(ServiceError) as exc:
                await update_agent_job_status(
                    db, 1, JobStatusUpdate(status="NOT_A_STATUS", fencing_token="tok"),
                )
        assert exc.value.status == 400

    @pytest.mark.asyncio
    async def test_terminal_requires_complete(self):
        job = MagicMock()
        db = AsyncMock()
        db.get = AsyncMock(return_value=job)
        with patch(
            "backend.services.agent_job_status.require_valid_runtime_lease",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ):
            with pytest.raises(ServiceError) as exc:
                await update_agent_job_status(
                    db, 1, JobStatusUpdate(status="FAILED", fencing_token="tok"),
                )
        assert exc.value.status == 409
        assert exc.value.detail["code"] == "TERMINAL_STATUS_REQUIRES_COMPLETE"

    @pytest.mark.asyncio
    async def test_non_running_invalid_transition(self):
        job = MagicMock()
        db = AsyncMock()
        db.get = AsyncMock(return_value=job)
        with patch(
            "backend.services.agent_job_status.require_valid_runtime_lease",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ):
            with pytest.raises(ServiceError) as exc:
                await update_agent_job_status(
                    db, 1, JobStatusUpdate(status="UNKNOWN", fencing_token="tok"),
                )
        assert exc.value.status == 409
        assert exc.value.detail["code"] == "INVALID_JOB_TRANSITION"

    @pytest.mark.asyncio
    async def test_running_is_heartbeat_noop(self):
        job = MagicMock()
        job.status = JobStatus.RUNNING.value
        job.status_reason = None
        job.updated_at = None
        db = AsyncMock()
        db.get = AsyncMock(return_value=job)
        db.commit = AsyncMock()
        with patch(
            "backend.services.agent_job_status.require_valid_runtime_lease",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ):
            out = await update_agent_job_status(
                db, 9,
                JobStatusUpdate(status="RUNNING", reason="tick", fencing_token="tok"),
            )
        assert out == {"job_id": 9, "status": job.status}
        assert job.status_reason == "tick"
        assert job.updated_at is not None
        db.commit.assert_awaited_once()
