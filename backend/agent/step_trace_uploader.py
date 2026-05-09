"""Background thread that uploads un-acked step_trace records to the server via HTTP.

Design mirrors OutboxDrainThread: daemon thread, start()/stop()/drain_sync() API,
_stop_event.wait(interval) loop.

Phase A (current): MQ is the primary write path; Uploader picks up acked=0 leftovers
  when Redis fails.  In practice it's a hot-standby补传.
Phase B (Phase 4): MQ XADD removed; Uploader becomes the sole upload path.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from backend.agent.registry.local_db import LocalDB

logger = logging.getLogger(__name__)


class StepTraceUploader:
    _BATCH_LIMIT = 100

    def __init__(
        self,
        api_url: str,
        local_db: "LocalDB",
        agent_secret: str = "",
        interval: float = 5.0,
    ):
        self._api_url = api_url
        self._local_db = local_db
        self._agent_secret = agent_secret
        self._interval = interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_id = 0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="step-trace-uploader",
        )
        self._thread.start()
        logger.info("step_trace_uploader_started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("step_trace_uploader_stopped")

    def drain_sync(self) -> int:
        """Blocking drain for shutdown — loops until no remaining data or error."""
        total = 0
        while True:
            try:
                n = self._upload_once()
            except Exception:
                logger.exception("step_trace_drain_error")
                break
            if n == 0:
                break
            total += n
        return total

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(self._interval)
            if self._stop_event.is_set():
                break
            try:
                self._upload_once()
            except Exception:
                logger.exception("step_trace_upload_error")

    def _upload_once(self) -> int:
        traces = self._local_db.get_unacked_traces(after_id=self._last_id)
        if not traces:
            return 0
        batch = traces[: self._BATCH_LIMIT]
        headers: Dict[str, str] = {}
        if self._agent_secret:
            headers["X-Agent-Secret"] = self._agent_secret
        resp = requests.post(
            f"{self._api_url}/api/v1/agent/steps",
            json=[_to_payload(t) for t in batch],
            headers=headers,
            timeout=15,
        )
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            if _status_code(exc) in (404, 409):
                return self._resolve_rejected_batch(batch, headers)
            raise
        for t in batch:
            self._local_db.mark_acked(t["id"])
            self._last_id = max(self._last_id, t["id"])
        logger.info("step_trace_uploaded count=%d", len(batch))
        return len(batch)

    def _resolve_rejected_batch(
        self,
        batch: List[Dict[str, Any]],
        headers: Dict[str, str],
    ) -> int:
        if len(batch) == 1:
            trace = batch[0]
            self._ack_rejected_trace(trace)
            self._last_id = max(self._last_id, trace["id"])
            return 1

        resolved = 0
        all_resolved = True
        for trace in batch:
            try:
                resp = requests.post(
                    f"{self._api_url}/api/v1/agent/steps",
                    json=[_to_payload(trace)],
                    headers=headers,
                    timeout=15,
                )
                resp.raise_for_status()
            except requests.HTTPError as exc:
                if _status_code(exc) in (404, 409):
                    self._ack_rejected_trace(trace)
                    resolved += 1
                    continue
                all_resolved = False
                logger.warning(
                    "step_trace_upload_retry trace_id=%s status=%s",
                    trace.get("id"), _status_code(exc),
                )
                break
            except Exception as exc:
                all_resolved = False
                logger.warning(
                    "step_trace_upload_retry trace_id=%s error=%s",
                    trace.get("id"), exc,
                )
                break

            self._local_db.mark_acked(trace["id"])
            resolved += 1

        if all_resolved:
            self._last_id = max(self._last_id, max(t["id"] for t in batch))
        logger.info("step_trace_upload_resolved count=%d/%d", resolved, len(batch))
        return resolved

    def _ack_rejected_trace(self, trace: Dict[str, Any]) -> None:
        self._local_db.mark_acked(trace["id"])
        logger.info(
            "step_trace_upload_rejected_ack trace_id=%s job_id=%s",
            trace.get("id"), trace.get("job_id"),
        )


def _status_code(exc: requests.HTTPError) -> Optional[int]:
    return exc.response.status_code if exc.response is not None else None


def _to_payload(trace: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a SQLite row dict to the StepTraceIn schema expected by the server."""
    return {
        "job_id": trace["job_id"],
        "step_id": trace["step_id"],
        "stage": trace.get("stage", "execute"),
        "event_type": trace["event_type"],
        "status": trace.get("status", ""),
        "output": trace.get("output"),
        "error_message": trace.get("error_message"),
        "original_ts": trace.get("original_ts"),
        "fencing_token": trace.get("fencing_token", ""),
    }
