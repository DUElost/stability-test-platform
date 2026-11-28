"""Control-plane regression tests for Issue #4222 Problem 2."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.job_timeout_config import (
    PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS,
    RUNNING_HEARTBEAT_TIMEOUT_SECONDS,
    running_heartbeat_timeout_seconds,
)
from backend.models.enums import JobStatus
from backend.services.agent_coordinator_heartbeat import (
    _CoordinatorHeartbeatIn,
    _CoordinatorHeartbeatJob,
    record_agent_coordinator_heartbeat,
)
from backend.services.agent_recovery import build_recovery_job_payload


def test_running_heartbeat_timeout_uses_running_budget_after_patrol_enters_teardown():
    now = datetime.now(timezone.utc)
    patrol_job = SimpleNamespace(
        pipeline_def={"lifecycle": {"patrol": {"interval_seconds": 60}}},
        patrol_cycle_count=288,
        last_patrol_heartbeat_at=now,
        current_patrol_step=None,
        execution_state="EXECUTING_STEP",
        disarm_guard=None,
        finishing_guard=None,
        disarm_results=None,
        updated_at=now,
        next_retry_at=None,
    )
    assert running_heartbeat_timeout_seconds(patrol_job) == PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS

    for overrides in (
        {"execution_state": "EXECUTING_DISARM"},
        {"disarm_guard": {"phase": "execute", "seq": 1}},
        {"disarm_results": {"power-cycle": {"result": "executed_unverified"}}},
        {"finishing_guard": {"kind": "teardown_disarm", "phase": "execute"}},
    ):
        teardown_job = SimpleNamespace(**{**patrol_job.__dict__, **overrides})
        assert running_heartbeat_timeout_seconds(teardown_job) == RUNNING_HEARTBEAT_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_coordinator_heartbeat_ignores_stale_disarm_execute_when_waiting_next_step():
    job = MagicMock()
    job.id = 211040
    job.host_id = "172-21-15-98"
    job.status = JobStatus.RUNNING.value
    job.disarm_guard = {"seq": 175, "phase": "execute", "step_id": "powercycle_disarm"}

    host = MagicMock()
    host.last_agent_instance_id = "inst-1"

    executed_statements = []

    class _Scalars:
        def __init__(self, first_val):
            self._first_val = first_val

        def first(self):
            return self._first_val

    class _Result:
        def __init__(self, first_val=None):
            self._first_val = first_val

        def scalars(self):
            return _Scalars(self._first_val)

    async def _get(model, key):
        if getattr(model, "__name__", "") == "Host":
            return host
        return None

    async def _execute(stmt, *args, **kwargs):
        executed_statements.append(stmt)
        if len(executed_statements) == 1:
            return _Result(job)
        return _Result(None)

    db = AsyncMock()
    db.get = AsyncMock(side_effect=_get)
    db.execute = AsyncMock(side_effect=_execute)
    db.run_sync = AsyncMock(side_effect=AssertionError("must not call accept_job_disarm_evidence for stale execute replay"))
    db.commit = AsyncMock()

    payload = _CoordinatorHeartbeatIn(
        host_id="172-21-15-98",
        agent_instance_id="inst-1",
        plan_run_hosts=[],
        jobs=[
            _CoordinatorHeartbeatJob(
                job_id=211040,
                execution_state="WAITING_EXECUTION_SLOT",
                disarm_evidence={"seq": 175, "phase": "execute", "step_id": "powercycle_disarm"},
            )
        ],
    )
    out = await record_agent_coordinator_heartbeat(db, payload)
    assert out.accepted is True
    assert out.disarm_guards == {}
    # Expect both SELECT(JobInstance) and UPDATE(JobInstance with WAITING_EXECUTION_SLOT + last_execution_heartbeat_at).
    assert len(executed_statements) == 2


@pytest.mark.asyncio
async def test_build_recovery_job_payload_marks_teardown_resumed():
    now = datetime.now(timezone.utc)
    job = SimpleNamespace(
        id=210996,
        plan_run_id=1762,
        plan_id=10,
        device_id=1766,
        host_id="172-21-15-98",
        status="UNKNOWN",
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        started_at=now - timedelta(hours=24),
        execution_state="EXECUTING_DISARM",
        disarm_guard={"phase": "execute", "step_id": "powercycle_disarm", "seq": 329},
        disarm_results={"power-cycle": {"result": "incomplete", "exit_code": 124}},
        finishing_guard=None,
        manual_action=None,
    )
    plan_run = SimpleNamespace(
        plan_snapshot={"plan": {}},
        run_context={},
    )
    prh = SimpleNamespace(id=55, total_job_count=8, device_count=8)

    class _PRHResult:
        def scalar_one_or_none(self):
            return prh

    db = AsyncMock()
    db.get = AsyncMock(return_value=plan_run)
    db.execute = AsyncMock(return_value=_PRHResult())

    with patch(
        "backend.services.agent_recovery._footprint_families_for_job_async",
        new=AsyncMock(return_value={"power-cycle"}),
    ):
        payload = await build_recovery_job_payload(
            db,
            job,
            device_serial="AT7QNX6B02000109",
            fencing_token="1766:16",
        )

    assert payload["teardown_resumed"] is True
    assert payload["footprint_families"] == ["power-cycle"]
    assert payload["disarm_results"]["power-cycle"]["result"] == "incomplete"
    assert payload["termination_reason"] == "timeout"
