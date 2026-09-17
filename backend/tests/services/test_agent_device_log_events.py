"""#1520 垂直切片：Agent DLE ingest 服务层直测。"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.models.enums import EventState
from backend.services.agent_device_log_events import (
    DeviceLogEventBatchIn,
    _ALLOWED_TRANSITIONS,
    _EXTRACTABLE_STATES,
    _parse_iso_dt,
    ingest_agent_device_log_events,
)


class TestParseIsoDt:
    def test_parses_zulu(self):
        dt = _parse_iso_dt("2026-09-17T01:02:03Z", "detected_at")
        assert dt.year == 2026
        assert dt.tzinfo is not None

    def test_rejects_garbage(self):
        with pytest.raises(HTTPException) as exc:
            _parse_iso_dt("not-a-date", "detected_at")
        assert exc.value.status_code == 400


class TestAllowedTransitions:
    def test_pull_failed_on_non_terminal(self):
        pull = EventState.PULL_FAILED.value
        for state, allowed in _ALLOWED_TRANSITIONS.items():
            if state in _EXTRACTABLE_STATES:
                assert pull not in allowed
            else:
                assert pull in allowed


class TestIngestGuards:
    @pytest.mark.asyncio
    async def test_empty_batch_returns_zero(self):
        out = await ingest_agent_device_log_events(
            db=None,  # type: ignore[arg-type]
            payload=DeviceLogEventBatchIn(events=[]),
        )
        assert out == {"upserted": 0, "total": 0}
