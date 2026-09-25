"""#1520 垂直切片：Agent batch lease renew 服务层直测。"""

from __future__ import annotations

import pytest
from backend.services.errors import ServiceError

from backend.services.agent_lease_extend import (
    _ExtendBatchIn,
    _LEASE_EXTEND_BATCH_MAX,
    _VALID_EXECUTION_STATES,
    _parse_progress_ts,
    extend_agent_leases_batch,
)


class TestParseProgressTs:
    def test_parses_iso_z(self):
        ts = _parse_progress_ts("2026-09-17T01:02:03Z")
        assert ts is not None
        assert ts.tzinfo is not None
        assert ts.year == 2026

    def test_rejects_empty_and_garbage(self):
        assert _parse_progress_ts("") is None
        assert _parse_progress_ts(None) is None
        assert _parse_progress_ts(123) is None
        assert _parse_progress_ts("not-a-date") is None


class TestValidExecutionStates:
    def test_known_states(self):
        assert "EXECUTING_STEP" in _VALID_EXECUTION_STATES
        assert "PATROL_SLEEP" in _VALID_EXECUTION_STATES
        assert "UNKNOWN" not in _VALID_EXECUTION_STATES


class TestExtendAgentLeasesBatchGuards:
    @pytest.mark.asyncio
    async def test_empty_batch_returns_empty(self):
        out = await extend_agent_leases_batch(
            db=None,  # type: ignore[arg-type]
            payload=_ExtendBatchIn(host_id="h1", leases=[]),
        )
        assert out.results == []

    @pytest.mark.asyncio
    async def test_batch_too_large_413(self):
        from backend.services.agent_lease_extend import _ExtendBatchItemIn

        items = [
            _ExtendBatchItemIn(job_id=i, fencing_token="t")
            for i in range(_LEASE_EXTEND_BATCH_MAX + 1)
        ]
        with pytest.raises(ServiceError) as exc:
            await extend_agent_leases_batch(
                db=None,  # type: ignore[arg-type]
                payload=_ExtendBatchIn(host_id="h1", leases=items),
            )
        assert exc.value.status == 413
        assert exc.value.detail["code"] == "LEASE_BATCH_TOO_LARGE"
