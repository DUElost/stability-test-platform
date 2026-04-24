"""终态 Outbox Drain 后台线程 —— 重试未确认的终端状态上报。

提取自 backend.agent.main，供 main.py barrel re-export。
"""

import logging
import os
import threading
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class OutboxDrainThread:
    """Background thread that retries un-acked terminal-state payloads."""

    _TERMINAL_STATUSES = {"COMPLETED", "FAILED", "ABORTED", "UNKNOWN"}

    def __init__(self, api_url: str, local_db, interval: float = 15.0):
        self._api_url = api_url
        self._local_db = local_db
        self._interval = interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._agent_secret = os.getenv("AGENT_SECRET", "")

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="outbox-drain",
        )
        self._thread.start()
        logger.info("outbox_drain_thread_started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("outbox_drain_thread_stopped")

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(self._interval)
            if self._stop_event.is_set():
                break
            try:
                self._drain_once()
            except Exception:
                logger.exception("outbox_drain_error")

    def drain_sync(self) -> int:
        """Blocking drain for shutdown — returns number of successfully sent items."""
        return self._drain_once()

    def _drain_once(self) -> int:
        pending = self._local_db.get_pending_terminals(limit=20)
        if not pending:
            self._local_db.prune_acked_terminals()
            return 0

        sent = 0
        headers = {"X-Agent-Secret": self._agent_secret} if self._agent_secret else {}
        for entry in pending:
            job_id = entry["job_id"]
            payload = entry["payload"]
            try:
                resp = requests.post(
                    f"{self._api_url}/api/v1/agent/jobs/{job_id}/complete",
                    json=payload,
                    headers=headers,
                    timeout=15,
                )
                resp.raise_for_status()
                self._local_db.ack_terminal(job_id)
                sent += 1
                logger.info("outbox_drain_acked job=%d", job_id)
            except requests.HTTPError as e:
                status_code = e.response.status_code if e.response else None
                if status_code == 409:
                    current = self._parse_current_status(e.response)
                    if current and current in self._TERMINAL_STATUSES:
                        self._local_db.ack_terminal(job_id)
                        sent += 1
                        logger.info(
                            "outbox_drain_conflict_ack job=%d current=%s (job is terminal)",
                            job_id, current,
                        )
                    else:
                        self._local_db.bump_terminal_attempt(job_id, str(e))
                        logger.warning(
                            "outbox_drain_conflict_retry job=%d current=%s",
                            job_id, current,
                        )
                elif status_code == 404:
                    self._local_db.ack_terminal(job_id)
                    logger.warning("outbox_drain_job_gone job=%d", job_id)
                else:
                    self._local_db.bump_terminal_attempt(job_id, str(e))
            except Exception as e:
                self._local_db.bump_terminal_attempt(job_id, str(e))
                logger.warning("outbox_drain_retry job=%d error=%s", job_id, e)

        self._local_db.prune_acked_terminals()
        return sent

    @staticmethod
    def _parse_current_status(response) -> Optional[str]:
        """Extract current_status from a 409 response body."""
        try:
            body = response.json()
            detail = body.get("detail", {})
            if isinstance(detail, dict):
                return detail.get("current_status")
            err = body.get("error", {})
            if isinstance(err, dict):
                return err.get("current_status")
        except Exception:
            pass
        return None
