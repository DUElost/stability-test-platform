"""设备锁续期管理器 - 后台线程定期续期活跃任务的设备锁。

提取自 backend.agent.main，供 main.py barrel re-export。
"""

import logging
import os
import threading
import time
from typing import Dict, Optional, Set

import requests

logger = logging.getLogger(__name__)


class LockRenewalManager:
    """设备锁续期管理器 - 后台线程定期续期活跃任务的设备锁"""

    def __init__(
        self,
        api_url: str,
        active_jobs_lock: threading.Lock,
        active_job_ids: Set[int],
        lock_renewal_stop_event: threading.Event,
    ):
        self._api_url = api_url
        self._jobs_lock = active_jobs_lock
        self._job_ids = active_job_ids
        self._stop_event = lock_renewal_stop_event
        self._thread: Optional[threading.Thread] = None
        self._agent_secret = os.getenv("AGENT_SECRET", "")
        self._post_retries = int(os.getenv("AGENT_POST_RETRIES", "3"))
        self._post_retry_base_delay = float(os.getenv("AGENT_POST_RETRY_BASE_DELAY", "1"))
        self._renewal_interval = int(os.getenv("AGENT_LOCK_RENEWAL_INTERVAL", "60"))
        self._fencing_tokens: Dict[int, str] = {}  # ADR-0019 Phase 2b

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._renewal_loop, daemon=True)
        self._thread.start()
        logger.info("lock_renewal_thread_started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("lock_renewal_thread_stopped")

    def _renewal_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._jobs_lock:
                current_jobs = list(self._job_ids)

            for job_id in current_jobs:
                if self._stop_event.is_set():
                    break

                try:
                    self._extend_lock(job_id)
                except Exception as e:
                    logger.warning(f"lock_renewal_failed for job {job_id}: {e}")

            self._stop_event.wait(self._renewal_interval)

    def set_fencing_token(self, job_id: int, token: str) -> None:
        """Store fencing_token for a job (ADR-0019 Phase 2b)."""
        with self._jobs_lock:
            self._fencing_tokens[job_id] = token

    def clear_fencing_token(self, job_id: int) -> None:
        """Remove fencing_token for a job (ADR-0019 Phase 2b)."""
        with self._jobs_lock:
            self._fencing_tokens.pop(job_id, None)

    def _extend_lock(self, job_id: int) -> None:
        with self._jobs_lock:
            token = self._fencing_tokens.get(job_id)
        if not token:
            logger.debug("extend_lock_skipped_no_token job_id=%s", job_id)
            return

        url = f"{self._api_url}/api/v1/agent/jobs/{job_id}/extend_lock"
        headers = {"X-Agent-Secret": self._agent_secret} if self._agent_secret else {}

        for attempt in range(1, self._post_retries + 1):
            try:
                resp = requests.post(
                    url, json={"fencing_token": token}, headers=headers, timeout=10,
                )
                resp.raise_for_status()
                result = resp.json()
                logger.debug(
                    f"lock_extended for job {job_id}, expires_at={result.get('expires_at')}"
                )
                return
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 409:
                    logger.error(
                        f"lock_lost for job {job_id}, removing from active jobs"
                    )
                    with self._jobs_lock:
                        self._job_ids.discard(job_id)
                        self._fencing_tokens.pop(job_id, None)
                    raise RuntimeError(f"Lock lost for job {job_id}")
                logger.warning(
                    f"lock_extension_attempt_{attempt}_failed for job {job_id}: {e}"
                )
            except requests.RequestException as e:
                logger.warning(
                    f"lock_extension_attempt_{attempt}_failed for job {job_id}: {e}"
                )

            if attempt < self._post_retries:
                delay = self._post_retry_base_delay * (2 ** (attempt - 1))
                time.sleep(delay)

        raise RuntimeError(
            f"Failed to extend lock for job {job_id} after {self._post_retries} attempts"
        )
