"""#1520 垂直切片：Agent complete / runtime lease 服务层直测。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.models.enums import JobStatus, LeaseStatus
from backend.services.agent_completion import (
    _RUN_TO_JOB,
    complete_agent_job,
    get_valid_runtime_lease,
)
from backend.services.agent_completion import _RunCompleteIn


class TestRunToJobMapping:
    def test_terminal_aliases(self):
        assert _RUN_TO_JOB["FINISHED"] == JobStatus.COMPLETED
        assert _RUN_TO_JOB["CANCELED"] == JobStatus.ABORTED
        assert _RUN_TO_JOB["CANCELLED"] == JobStatus.ABORTED
        assert _RUN_TO_JOB["FAILED"] == JobStatus.FAILED


class TestGetValidRuntimeLease:
    @pytest.mark.asyncio
    async def test_rejects_expired_lease(self):
        now = datetime.now(timezone.utc)
        job = SimpleNamespace(id=1, device_id=2, status=JobStatus.RUNNING.value)
        lease = SimpleNamespace(
            fencing_token="tok",
            expires_at=now - timedelta(seconds=1),
            status=LeaseStatus.ACTIVE.value,
        )

        class _Result:
            def scalars(self):
                return self

            def first(self):
                return lease

        class _Db:
            async def execute(self, *_a, **_k):
                return _Result()

        got = await get_valid_runtime_lease(_Db(), job, "tok")  # type: ignore[arg-type]
        assert got is None

    @pytest.mark.asyncio
    async def test_rejects_token_mismatch(self):
        now = datetime.now(timezone.utc)
        job = SimpleNamespace(id=1, device_id=2, status=JobStatus.RUNNING.value)
        lease = SimpleNamespace(
            fencing_token="good",
            expires_at=now + timedelta(seconds=60),
            status=LeaseStatus.ACTIVE.value,
        )

        class _Result:
            def scalars(self):
                return self

            def first(self):
                return lease

        class _Db:
            async def execute(self, *_a, **_k):
                return _Result()

        got = await get_valid_runtime_lease(_Db(), job, "bad")  # type: ignore[arg-type]
        assert got is None

    @pytest.mark.asyncio
    async def test_rejects_non_running_unless_allowed(self):
        now = datetime.now(timezone.utc)
        job = SimpleNamespace(id=1, device_id=2, status=JobStatus.UNKNOWN.value)
        lease = SimpleNamespace(
            fencing_token="tok",
            expires_at=now + timedelta(seconds=60),
            status=LeaseStatus.ACTIVE.value,
        )

        class _Result:
            def scalars(self):
                return self

            def first(self):
                return lease

        class _Db:
            async def execute(self, *_a, **_k):
                return _Result()

        assert await get_valid_runtime_lease(_Db(), job, "tok") is None  # type: ignore[arg-type]
        got = await get_valid_runtime_lease(
            _Db(), job, "tok",  # type: ignore[arg-type]
            allowed_job_statuses={JobStatus.UNKNOWN.value},
        )
        assert got is lease


class TestCompleteAgentJobGuards:
    @pytest.mark.asyncio
    async def test_invalid_terminal_status_400(self):
        job = SimpleNamespace(id=9, status=JobStatus.RUNNING.value)

        class _Result:
            def scalars(self):
                return self

            def first(self):
                return job

        class _Db:
            async def execute(self, *_a, **_k):
                return _Result()

        with pytest.raises(HTTPException) as exc:
            await complete_agent_job(
                _Db(),  # type: ignore[arg-type]
                9,
                _RunCompleteIn(
                    update={"status": "NOT_A_STATUS"},
                    fencing_token="t",
                ),
            )
        assert exc.value.status_code == 400
        assert exc.value.detail["code"] == "INVALID_TERMINAL_STATUS"

    @pytest.mark.asyncio
    async def test_running_status_not_terminal_400(self):
        job = SimpleNamespace(id=9, status=JobStatus.RUNNING.value)

        class _Result:
            def scalars(self):
                return self

            def first(self):
                return job

        class _Db:
            async def execute(self, *_a, **_k):
                return _Result()

        with pytest.raises(HTTPException) as exc:
            await complete_agent_job(
                _Db(),  # type: ignore[arg-type]
                9,
                _RunCompleteIn(
                    update={"status": "RUNNING"},
                    fencing_token="t",
                ),
            )
        assert exc.value.status_code == 400
        assert exc.value.detail["code"] == "INVALID_TERMINAL_STATUS"
