"""#1520 垂直切片：Agent log_signals 摄取服务层直测。"""

from __future__ import annotations

import pytest

from backend.models.enums import JobStatus
from backend.services.agent_log_signals import (
    LogSignalBatchIn,
    TERMINAL_JOB_STATUSES,
    ingest_agent_log_signals,
)


class TestTerminalJobStatuses:
    def test_includes_completed_failed_aborted(self):
        assert JobStatus.COMPLETED.value in TERMINAL_JOB_STATUSES
        assert JobStatus.FAILED.value in TERMINAL_JOB_STATUSES
        assert JobStatus.ABORTED.value in TERMINAL_JOB_STATUSES
        assert JobStatus.UNKNOWN.value not in TERMINAL_JOB_STATUSES


class TestIngestGuards:
    @pytest.mark.asyncio
    async def test_empty_batch_returns_zero(self):
        out = await ingest_agent_log_signals(
            db=None,  # type: ignore[arg-type]
            payload=LogSignalBatchIn(signals=[]),
        )
        assert out == {"inserted": 0, "total": 0}
