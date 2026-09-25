"""#1520 垂直切片：Agent step_status / step traces 服务层直测。"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.services.agent_step_status import (
    StepStatusIn,
    StepTraceIn,
    update_agent_job_step_status,
    upload_agent_step_traces,
)
from backend.services.errors import Conflict, ServiceError


class TestUploadStepTracesGuards:
    @pytest.mark.asyncio
    async def test_job_not_found_404(self):
        db = AsyncMock()
        db.get = AsyncMock(return_value=None)
        with pytest.raises(ServiceError) as exc:
            await upload_agent_step_traces(
                db,
                [StepTraceIn(
                    job_id=1,
                    step_id="s1",
                    event_type="status_update",
                    fencing_token="tok",
                )],
            )
        assert exc.value.status == 404

    @pytest.mark.asyncio
    async def test_invalid_token_409(self):
        job = MagicMock()
        db = AsyncMock()
        db.get = AsyncMock(return_value=job)
        with patch(
            "backend.services.agent_step_status.require_valid_runtime_lease",
            new_callable=AsyncMock,
            side_effect=Conflict("invalid or expired fencing_token"),
        ):
            with pytest.raises(ServiceError) as exc:
                await upload_agent_step_traces(
                    db,
                    [StepTraceIn(
                        job_id=1,
                        step_id="s1",
                        event_type="status_update",
                        fencing_token="bad",
                    )],
                )
        assert exc.value.status == 409


class TestUpdateStepStatusGuards:
    @pytest.mark.asyncio
    async def test_job_not_found_404(self):
        db = AsyncMock()
        db.get = AsyncMock(return_value=None)
        with pytest.raises(ServiceError) as exc:
            await update_agent_job_step_status(
                db, 1, "s1", StepStatusIn(status="RUNNING", fencing_token="tok"),
            )
        assert exc.value.status == 404

    @pytest.mark.asyncio
    async def test_derives_stable_trace_event_id(self):
        job = MagicMock()
        job.plan_run_id = 10
        db = AsyncMock()
        db.get = AsyncMock(return_value=job)
        with patch(
            "backend.services.agent_step_status.require_valid_runtime_lease",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ), patch(
            "backend.services.agent_step_status.reconcile_step_traces",
            new_callable=AsyncMock,
            return_value={"transitioned_jobs": [], "inserted": 1},
        ) as reconcile:
            out = await update_agent_job_step_status(
                db, 7, "flash",
                StepStatusIn(status="FAILED", exit_code=1, fencing_token="tok"),
            )
        assert out["job_id"] == 7
        assert out["step_id"] == "flash"
        raw = reconcile.await_args.args[1][0]
        assert raw["trace_event_id"].startswith("status:")
        assert len(raw["trace_event_id"]) == len("status:") + 24
