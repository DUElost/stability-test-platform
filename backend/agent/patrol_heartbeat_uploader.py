"""ADR-0022 — Agent-side patrol heartbeat uploader.

Per-cycle patrol aggregate uploads.  Replaces the per-step step_trace
write path for the patrol stage (init/teardown stays unchanged).

Design choices:
- **Synchronous send**: every patrol cycle is at least 1s long; the
  heartbeat HTTP call (~10ms typical, 5s worst) is cheap relative to
  the cycle and we need its ACK to read the pending ``manual_action``
  back from the server.  Returning the server response inline is much
  simpler than a polling loop.
- **No SQLite outbox**: heartbeats are "near-real-time" — losing one
  is fine because the next cycle's GREATEST() update on the server
  side reconciles the counter.  An outbox would add complexity for
  zero benefit.
- **Best-effort retries**: a failed POST logs and returns None; the
  next cycle retries naturally.  We do **not** block the patrol loop
  on network flakiness.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10.0
_DEFAULT_BACKOFF_BASE = 60.0
_DEFAULT_BACKOFF_GROWTH = 2.0
_DEFAULT_BACKOFF_MAX = 3600.0


class PatrolHeartbeatUploader:
    """Lightweight per-call HTTP poster for patrol heartbeats.

    Stateless aside from API config; safe to share across patrol loops
    on different jobs.  Returned dict mirrors the server's
    ``PatrolHeartbeatOut`` schema: callers can read
    ``manual_action`` to short-circuit patrol or pre-empt sleep.
    """

    def __init__(self, api_url: str, agent_secret: str = "") -> None:
        self._api_url = api_url.rstrip("/")
        self._agent_secret = agent_secret

    def send(
        self,
        *,
        job_id: int,
        fencing_token: str,
        cycle_index: int,
        success_delta: int = 0,
        failed_delta: int = 0,
        current_step: Optional[str] = None,
        current_failure_streak: int = 0,
        next_retry_at: Optional[datetime] = None,
        manual_action_observed: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Send one patrol heartbeat.  Returns the server's response dict
        (containing ``manual_action`` etc.) or None on failure.

        Failure modes return None so the patrol loop continues; the next
        cycle's GREATEST() update on the server reconciles missed counters.
        """
        url = f"{self._api_url}/api/v1/agent/jobs/{job_id}/patrol-heartbeat"
        headers: Dict[str, str] = {}
        if self._agent_secret:
            headers["X-Agent-Secret"] = self._agent_secret

        payload: Dict[str, Any] = {
            "fencing_token":          fencing_token,
            "cycle_index":            cycle_index,
            "success_delta":          success_delta,
            "failed_delta":           failed_delta,
            "current_failure_streak": current_failure_streak,
        }
        if current_step is not None:
            payload["current_step"] = current_step
        if next_retry_at is not None:
            payload["next_retry_at"] = next_retry_at.isoformat()
        if manual_action_observed:
            payload["manual_action_observed"] = manual_action_observed

        try:
            resp = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            logger.warning(
                "patrol_heartbeat_post_failed job=%d cycle=%d err=%s",
                job_id, cycle_index, exc,
            )
            return None

        if resp.status_code == 409:
            # Lease lost — Agent's LeaseRenewer / pipeline_engine will detect
            # and abort the run via its own path.  We don't surface this here.
            logger.warning(
                "patrol_heartbeat_lease_invalid job=%d cycle=%d status=409",
                job_id, cycle_index,
            )
            return None

        if resp.status_code >= 400:
            logger.warning(
                "patrol_heartbeat_rejected job=%d cycle=%d status=%d body=%s",
                job_id, cycle_index, resp.status_code,
                resp.text[:300] if resp.text else "",
            )
            return None

        try:
            body = resp.json()
        except ValueError:
            logger.warning(
                "patrol_heartbeat_response_not_json job=%d cycle=%d body=%s",
                job_id, cycle_index, resp.text[:200] if resp.text else "",
            )
            return None

        # Unwrap ApiResponse envelope: {"data": {...}, "error": null, ...}
        data = body.get("data") if isinstance(body, dict) else None
        return data if isinstance(data, dict) else None


def compute_backoff_seconds(
    streak: int,
    *,
    base_seconds: float = _DEFAULT_BACKOFF_BASE,
    growth_factor: float = _DEFAULT_BACKOFF_GROWTH,
    max_seconds: float = _DEFAULT_BACKOFF_MAX,
) -> float:
    """ADR-0022 D4: ``min(base * growth^max(0, streak-2), max)``.

    streak 1-2 → base (no extra wait).
    streak 3   → base * growth (5min by default).
    streak 7+  → max (1h by default).

    Caller is responsible for handling streak == 0 (no backoff needed).
    """
    if streak <= 0:
        return 0.0
    if streak <= 2:
        return base_seconds
    raw = base_seconds * (growth_factor ** (streak - 2))
    return min(raw, max_seconds)
