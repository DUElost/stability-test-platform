"""Coalesced dashboard summary publisher (#2324)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.services import dashboard_summary_publisher as pub


@pytest.mark.asyncio
async def test_schedule_coalesces_to_single_broadcast(monkeypatch):
    monkeypatch.setenv("STP_DASHBOARD_SUMMARY_PUSH_INTERVAL_SECONDS", "0.05")
    pub._reset_for_tests()

    loop = asyncio.get_running_loop()
    pub.bind_event_loop(loop)

    summary = {
        "hosts": {"total": 1},
        "devices": {"total": 0},
        "alerts": {"total": 0},
        "host_resources": [],
    }
    broadcast = AsyncMock()

    with patch(
        "backend.services.dashboard_summary_publisher.compute_dashboard_summary",
        return_value=summary,
    ), patch(
        "backend.services.dashboard_summary_publisher.SessionLocal",
    ) as session_local, patch(
        "backend.realtime.socketio_server.broadcast_dashboard_summary",
        broadcast,
    ), patch(
        "backend.services.dashboard_summary_publisher.dashboard_summary_push_total",
    ) as counter:
        session_local.return_value.close = lambda: None

        for _ in range(5):
            pub.schedule_dashboard_summary_push()

        await asyncio.sleep(0.2)

        assert broadcast.await_count == 1
        broadcast.assert_awaited_once_with(summary)
        assert counter.inc.call_count == 1

    pub._reset_for_tests()
