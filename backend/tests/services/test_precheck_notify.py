"""Unit tests for precheck notify debounce + NotifyPayload."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from backend.services.precheck.notify import (
    NotifyPayload,
    PRECHECK_NOTIFY_DEBOUNCE_SECONDS,
    emit_dispatch_gate_invalidation,
    reset_notify_debounce_state,
)


@pytest.fixture(autouse=True)
def _reset_debounce():
    reset_notify_debounce_state()
    yield
    reset_notify_debounce_state()


class TestNotifyPayload:
    def test_merge_prefers_latest_non_null_fields(self):
        left = NotifyPayload(phase="verifying", dispatch_status=None)
        right = NotifyPayload(phase=None, dispatch_status="running")
        assert left.merge(right) == NotifyPayload(
            phase="verifying", dispatch_status="running",
        )

    def test_should_flush_on_terminal_phase_or_dispatch_status(self):
        assert NotifyPayload(phase="ready").should_flush_immediately(None)
        assert NotifyPayload(phase="failed").should_flush_immediately(None)
        assert NotifyPayload(dispatch_status="completed").should_flush_immediately(None)
        assert NotifyPayload(dispatch_status="failed").should_flush_immediately(None)

    def test_should_flush_on_phase_transition(self):
        prior = NotifyPayload(phase="verifying")
        incoming = NotifyPayload(phase="syncing")
        assert incoming.should_flush_immediately(prior)


class TestNotifyDebounce:
    def test_zero_debounce_emits_immediately(self, monkeypatch):
        monkeypatch.setattr(
            "backend.services.precheck.notify.PRECHECK_NOTIFY_DEBOUNCE_SECONDS", 0,
        )
        captured: list[tuple] = []

        def fake_schedule_emit(event, data, namespace="/dashboard", room=None):
            captured.append((event, data, namespace, room))

        with patch(
            "backend.realtime.socketio_server.schedule_emit",
            side_effect=fake_schedule_emit,
        ):
            emit_dispatch_gate_invalidation(42, phase="verifying")
            emit_dispatch_gate_invalidation(42, phase="verifying")

        assert len(captured) == 2

    def test_debounce_coalesces_same_phase(self, monkeypatch):
        monkeypatch.setattr(
            "backend.services.precheck.notify.PRECHECK_NOTIFY_DEBOUNCE_SECONDS", 0.05,
        )
        captured: list[tuple] = []

        def fake_schedule_emit(event, data, namespace="/dashboard", room=None):
            captured.append((event, data, namespace, room))

        with patch(
            "backend.realtime.socketio_server.schedule_emit",
            side_effect=fake_schedule_emit,
        ):
            emit_dispatch_gate_invalidation(7, phase="syncing")
            emit_dispatch_gate_invalidation(7, phase="syncing")
            time.sleep(0.08)

        assert len(captured) == 1
        assert captured[0][1]["payload"]["phase"] == "syncing"

    def test_phase_transition_flushes_immediately(self, monkeypatch):
        monkeypatch.setattr(
            "backend.services.precheck.notify.PRECHECK_NOTIFY_DEBOUNCE_SECONDS", 1.0,
        )
        captured: list[tuple] = []

        def fake_schedule_emit(event, data, namespace="/dashboard", room=None):
            captured.append((event, data, namespace, room))

        with patch(
            "backend.realtime.socketio_server.schedule_emit",
            side_effect=fake_schedule_emit,
        ):
            emit_dispatch_gate_invalidation(9, phase="verifying")
            emit_dispatch_gate_invalidation(9, phase="syncing")

        assert len(captured) == 2
        phases = [item[1]["payload"]["phase"] for item in captured]
        assert phases == ["verifying", "syncing"]
