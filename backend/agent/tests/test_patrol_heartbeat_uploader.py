# -*- coding: utf-8 -*-
"""ADR-0022 — PatrolHeartbeatUploader unit tests.

Covers compute_backoff_seconds() formula and the synchronous send() path:
  - 200 OK ACK is unwrapped from the {"data": {...}} envelope
  - 4xx (lease invalid / bad request) returns None
  - Network exception returns None
  - Optional fields (current_step / next_retry_at / manual_action_observed)
    are only included when set
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.agent.patrol_heartbeat_uploader import (
    PatrolHeartbeatUploader,
    compute_backoff_seconds,
)


# ---------------------------------------------------------------------------
# compute_backoff_seconds — D4 formula
# ---------------------------------------------------------------------------


class TestComputeBackoff:
    @pytest.mark.parametrize(
        "streak,expected",
        [
            (0, 0.0),
            (1, 60.0),       # streak<=2 → base
            (2, 60.0),
            (3, 120.0),      # 60 * 2^1
            (4, 240.0),      # 60 * 2^2
            (5, 480.0),      # 60 * 2^3
            (6, 960.0),      # 60 * 2^4
            (7, 1920.0),     # 60 * 2^5
            (8, 3600.0),     # 60 * 2^6 = 3840 → capped at 3600
            (100, 3600.0),   # always capped at max
        ],
    )
    def test_default_formula(self, streak, expected):
        assert compute_backoff_seconds(streak) == expected

    def test_negative_streak_zero(self):
        assert compute_backoff_seconds(-1) == 0.0
        assert compute_backoff_seconds(-100) == 0.0

    def test_custom_policy(self):
        # base=120, growth=1.5, max=900
        # streak 3 → 120 * 1.5 = 180
        # streak 4 → 120 * 2.25 = 270
        # streak 5 → 120 * 3.375 = 405
        # streak 6 → 120 * 5.0625 = 607.5
        # streak 7 → 120 * 7.59 = 911 → capped 900
        assert compute_backoff_seconds(
            3, base_seconds=120.0, growth_factor=1.5, max_seconds=900.0
        ) == 180.0
        assert compute_backoff_seconds(
            7, base_seconds=120.0, growth_factor=1.5, max_seconds=900.0
        ) == 900.0


# ---------------------------------------------------------------------------
# PatrolHeartbeatUploader.send — HTTP path
# ---------------------------------------------------------------------------


@pytest.fixture
def uploader():
    return PatrolHeartbeatUploader(
        api_url="http://test-api:8000",
        agent_secret="test-secret",
    )


def _ok_response(payload_data: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"data": payload_data, "error": None}
    return resp


class TestPatrolHeartbeatSend:
    def test_send_minimal_payload_returns_unwrapped_data(self, uploader):
        with patch("backend.agent.patrol_heartbeat_uploader.requests.post") as mock_post:
            mock_post.return_value = _ok_response({
                "job_id": 42,
                "patrol_cycle_count": 14,
                "patrol_success_cycle_count": 13,
                "patrol_failed_cycle_count": 1,
                "current_failure_streak": 0,
                "next_retry_at": None,
                "manual_action": None,
            })
            result = uploader.send(
                job_id=42,
                fencing_token="tok-1",
                cycle_index=14,
                success_delta=1,
                failed_delta=0,
            )

        assert result == {
            "job_id": 42,
            "patrol_cycle_count": 14,
            "patrol_success_cycle_count": 13,
            "patrol_failed_cycle_count": 1,
            "current_failure_streak": 0,
            "next_retry_at": None,
            "manual_action": None,
        }

        # URL + headers + body
        args, kwargs = mock_post.call_args
        assert args[0] == "http://test-api:8000/api/v1/agent/jobs/42/patrol-heartbeat"
        assert kwargs["headers"] == {"X-Agent-Secret": "test-secret"}
        body = kwargs["json"]
        assert body["fencing_token"] == "tok-1"
        assert body["cycle_index"] == 14
        assert body["success_delta"] == 1
        assert body["failed_delta"] == 0
        assert "current_step" not in body
        assert "next_retry_at" not in body
        assert "manual_action_observed" not in body

    def test_send_full_payload_includes_optional_fields(self, uploader):
        next_retry = datetime(2026, 5, 8, 14, 30, 0, tzinfo=timezone.utc)
        with patch("backend.agent.patrol_heartbeat_uploader.requests.post") as mock_post:
            mock_post.return_value = _ok_response({
                "job_id": 42,
                "patrol_cycle_count": 14,
                "patrol_success_cycle_count": 10,
                "patrol_failed_cycle_count": 4,
                "current_failure_streak": 4,
                "next_retry_at": next_retry.isoformat(),
                "manual_action": "EXIT_REQUESTED",
            })
            result = uploader.send(
                job_id=42,
                fencing_token="tok-1",
                cycle_index=14,
                success_delta=0,
                failed_delta=1,
                current_step="patrol.monkey_check",
                current_failure_streak=4,
                next_retry_at=next_retry,
                manual_action_observed="RETRY_NOW",
            )

        body = mock_post.call_args.kwargs["json"]
        assert body["current_step"] == "patrol.monkey_check"
        assert body["next_retry_at"] == next_retry.isoformat()
        assert body["current_failure_streak"] == 4
        assert body["manual_action_observed"] == "RETRY_NOW"
        assert result["manual_action"] == "EXIT_REQUESTED"

    def test_409_job_not_running_returns_sentinel_and_invokes_callback(self, uploader):
        """JOB_NOT_RUNNING → sentinel dict + optional callback; patrol loop can stop."""
        callback = MagicMock()
        uploader._on_job_not_running = callback
        with patch("backend.agent.patrol_heartbeat_uploader.requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=409,
                text='{"detail":{"code":"JOB_NOT_RUNNING"}}',
                json=lambda: {"detail": {"code": "JOB_NOT_RUNNING"}},
            )
            result = uploader.send(
                job_id=42, fencing_token="tok", cycle_index=1,
            )
        assert result == {"_job_not_running": True}
        callback.assert_called_once_with(42)

    def test_409_other_returns_none(self, uploader):
        """Non-JOB_NOT_RUNNING 409 (lease invalid) still returns None."""
        with patch("backend.agent.patrol_heartbeat_uploader.requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=409,
                text="invalid lease",
                json=lambda: {"detail": "invalid or expired fencing_token"},
            )
            result = uploader.send(
                job_id=42, fencing_token="tok", cycle_index=1,
            )
        assert result is None

    def test_409_returns_none_legacy(self, uploader):
        """Lease invalid without parseable code: best-effort returns None."""
        with patch("backend.agent.patrol_heartbeat_uploader.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=409, text="invalid lease")
            mock_post.return_value.json.side_effect = ValueError("bad json")
            result = uploader.send(
                job_id=42, fencing_token="tok", cycle_index=1,
            )
        assert result is None

    def test_400_returns_none(self, uploader):
        with patch("backend.agent.patrol_heartbeat_uploader.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=400, text="bad delta")
            result = uploader.send(
                job_id=42, fencing_token="tok", cycle_index=1, success_delta=-1,
            )
        assert result is None

    def test_request_exception_returns_none(self, uploader):
        import requests

        with patch("backend.agent.patrol_heartbeat_uploader.requests.post") as mock_post:
            mock_post.side_effect = requests.ConnectionError("boom")
            result = uploader.send(
                job_id=42, fencing_token="tok", cycle_index=1,
            )
        assert result is None

    def test_invalid_json_returns_none(self, uploader):
        with patch("backend.agent.patrol_heartbeat_uploader.requests.post") as mock_post:
            resp = MagicMock(status_code=200, text="not json")
            resp.json.side_effect = ValueError("invalid json")
            mock_post.return_value = resp
            result = uploader.send(
                job_id=42, fencing_token="tok", cycle_index=1,
            )
        assert result is None

    def test_no_agent_secret_omits_header(self):
        uploader = PatrolHeartbeatUploader(api_url="http://x", agent_secret="")
        with patch("backend.agent.patrol_heartbeat_uploader.requests.post") as mock_post:
            mock_post.return_value = _ok_response({
                "job_id": 1,
                "patrol_cycle_count": 1,
                "patrol_success_cycle_count": 1,
                "patrol_failed_cycle_count": 0,
                "current_failure_streak": 0,
                "next_retry_at": None,
                "manual_action": None,
            })
            uploader.send(job_id=1, fencing_token="tok", cycle_index=1)
        assert mock_post.call_args.kwargs["headers"] == {}
