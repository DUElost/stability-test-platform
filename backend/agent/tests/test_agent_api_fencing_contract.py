from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.api.routes.agent_api import (
    JobStatusUpdate,
    StepTraceIn,
    _CoordinatorHeartbeatIn,
    _StepStatusIn,
    coordinator_heartbeat,
    update_job_status,
    update_job_step_status,
    upload_step_traces,
)
from backend.models.enums import JobStatus


@pytest.mark.asyncio
async def test_coordinator_heartbeat_persists_valid_plan_run_host_phase():
    row = MagicMock()
    row.host_id = "host-1"
    row.plan_run_id = 22
    row.coordinator_epoch = 2
    row.phase = None

    db = MagicMock()
    db.get = AsyncMock(return_value=row)
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    result = await coordinator_heartbeat(
        payload=_CoordinatorHeartbeatIn(
            host_id="host-1",
            agent_instance_id="agent-1",
            plan_run_hosts=[{
                "id": 11,
                "plan_run_id": 22,
                "host_id": "host-1",
                "coordinator_epoch": 2,
                "phase": "PATROL",
            }],
            jobs=[],
        ),
        db=db,
        _=None,
    )

    assert result.data.accepted is True
    assert row.phase == "PATROL"
    assert row.coordinator_heartbeat_at is not None
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_coordinator_heartbeat_rejects_old_agent_instance():
    host = MagicMock()
    host.last_agent_instance_id = "new-agent"
    row = MagicMock()
    row.host_id = "host-1"
    row.coordinator_epoch = 4
    row.phase = None

    db = MagicMock()
    db.get = AsyncMock(side_effect=[host, row])
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    result = await coordinator_heartbeat(
        payload=_CoordinatorHeartbeatIn(
            host_id="host-1",
            agent_instance_id="old-agent",
            plan_run_hosts=[{
                "id": 11,
                "plan_run_id": 22,
                "host_id": "host-1",
                "coordinator_epoch": 99,
                "phase": "PATROL",
            }],
            jobs=[{"job_id": 101, "execution_state": "EXECUTING_STEP"}],
        ),
        db=db,
        _=None,
    )

    assert result.data.accepted is False
    assert result.data.agent_instance_stale is True
    assert result.data.stale_plan_run_host_ids == [11]
    assert result.data.current_coordinator_epochs == {11: 4}
    assert row.phase is None


@pytest.mark.asyncio
async def test_update_job_status_rejects_invalid_fencing_token_before_transition():
    job = MagicMock()
    job.id = 101
    job.device_id = 201
    job.status = JobStatus.RUNNING.value

    db = MagicMock()
    db.get = AsyncMock(return_value=job)
    db.commit = AsyncMock()

    with patch(
        "backend.api.routes.agent_api._get_valid_runtime_lease",
        new=AsyncMock(return_value=None),
    ) as mock_validate:
        with pytest.raises(HTTPException) as exc_info:
            await update_job_status(
                job_id=job.id,
                payload=JobStatusUpdate(
                    status=JobStatus.UNKNOWN.value,
                    fencing_token="WRONG_TOKEN",
                ),
                db=db,
                _=None,
            )

    assert exc_info.value.status_code == 409
    assert "fencing_token" in exc_info.value.detail.lower()
    mock_validate.assert_awaited_once()
    db.commit.assert_not_awaited()


def test_update_job_status_missing_fencing_token_rejected_by_schema():
    with pytest.raises(ValidationError):
        JobStatusUpdate(status=JobStatus.UNKNOWN.value)


@pytest.mark.asyncio
async def test_upload_step_traces_rejects_invalid_fencing_token_before_reconcile():
    job = MagicMock()
    job.id = 102
    job.device_id = 202
    job.status = JobStatus.RUNNING.value

    db = MagicMock()
    db.get = AsyncMock(return_value=job)

    with patch(
        "backend.api.routes.agent_api._get_valid_runtime_lease",
        new=AsyncMock(return_value=None),
    ) as mock_validate, patch(
        "backend.api.routes.agent_api.reconcile_step_traces",
        new=AsyncMock(return_value=1),
    ) as mock_reconcile:
        with pytest.raises(HTTPException) as exc_info:
            await upload_step_traces(
                traces=[
                    StepTraceIn(
                        job_id=job.id,
                        step_id="step-1",
                        event_type="FAILED",
                        status="FAILED",
                        fencing_token="WRONG_TOKEN",
                    )
                ],
                db=db,
                _=None,
            )

    assert exc_info.value.status_code == 409
    assert "fencing_token" in exc_info.value.detail.lower()
    mock_validate.assert_awaited_once()
    mock_reconcile.assert_not_awaited()


def test_upload_step_traces_missing_fencing_token_rejected_by_schema():
    with pytest.raises(ValidationError):
        StepTraceIn(
            job_id=103,
            step_id="step-1",
            event_type="COMPLETED",
            status="COMPLETED",
        )


@pytest.mark.asyncio
async def test_update_job_step_status_rejects_invalid_fencing_token_before_reconcile():
    job = MagicMock()
    job.id = 104
    job.device_id = 204
    job.status = JobStatus.RUNNING.value

    db = MagicMock()
    db.get = AsyncMock(return_value=job)

    with patch(
        "backend.api.routes.agent_api._get_valid_runtime_lease",
        new=AsyncMock(return_value=None),
    ) as mock_validate, patch(
        "backend.services.reconciler.reconcile_step_traces",
        new=AsyncMock(return_value=1),
    ) as mock_reconcile:
        with pytest.raises(HTTPException) as exc_info:
            await update_job_step_status(
                job_id=job.id,
                step_id="step-1",
                payload=_StepStatusIn(
                    status="FAILED",
                    fencing_token="WRONG_TOKEN",
                ),
                db=db,
                _=None,
            )

    assert exc_info.value.status_code == 409
    assert "fencing_token" in exc_info.value.detail.lower()
    mock_validate.assert_awaited_once()
    mock_reconcile.assert_not_awaited()


def test_update_job_step_status_missing_fencing_token_rejected_by_schema():
    with pytest.raises(ValidationError):
        _StepStatusIn(status="FAILED")
