"""DeviceLease 续租器 —— 后台线程定期续期活跃 Job 的 device lease。

提取自 backend.agent.main，供 main.py barrel re-export。

ADR-0019 Phase 3b: LockRenewalManager 重命名为 LeaseRenewer，补齐 409 清理闭环，
统一结构化日志，明确 renew_interval < lease_ttl/2 配置关系。
"""

import logging
import os
import threading
import time
from typing import Callable, Dict, Optional, Set

import requests

logger = logging.getLogger(__name__)

# Backend lease_manager.py:_DEFAULT_LEASE_SECONDS = 600
_BACKEND_LEASE_TTL = 600


class LeaseRenewer:
    """DeviceLease 续租器 —— 后台线程定期续期活跃 Job 的 device lease."""

    def __init__(
        self,
        api_url: str,
        active_jobs_lock: threading.Lock,
        active_job_ids: Set[int],
        lock_renewal_stop_event: threading.Event,
        agent_instance_id: str = "",  # ADR-0019 Phase 3a
        on_lease_lost: Optional[Callable[[int, Optional[int]], None]] = None,  # Phase 3b
    ):
        self._api_url = api_url
        self._jobs_lock = active_jobs_lock
        self._job_ids = active_job_ids
        self._stop_event = lock_renewal_stop_event
        self._thread: Optional[threading.Thread] = None
        self._agent_secret = os.getenv("AGENT_SECRET", "")
        self._post_retries = max(int(os.getenv("AGENT_POST_RETRIES", "3")), 1)
        self._post_retry_base_delay = float(os.getenv("AGENT_POST_RETRY_BASE_DELAY", "1"))
        self._renewal_interval = int(os.getenv("AGENT_LOCK_RENEWAL_INTERVAL", "60"))
        self._fencing_tokens: Dict[int, str] = {}  # ADR-0019 Phase 2b
        self._device_ids: Dict[int, int] = {}      # Phase 3b: job_id → device_id
        self._agent_instance_id = agent_instance_id
        self._on_lease_lost = on_lease_lost

        # Phase 3b: validate renew_interval < lease_ttl / 2
        lease_ttl_env = int(os.getenv("AGENT_LEASE_TTL", str(_BACKEND_LEASE_TTL)))
        if self._renewal_interval >= lease_ttl_env / 2:
            logger.warning("lease_renewal_interval_too_long", extra={
                "agent_instance_id": self._agent_instance_id,
                "renewal_interval": self._renewal_interval,
                "lease_ttl": lease_ttl_env,
                "required_max": lease_ttl_env / 2,
                "reason": "renewal_interval should be < lease_ttl/2 to ensure at least one renewal before expiration",
            })

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._renewal_loop, daemon=True)
        self._thread.start()
        logger.info("lease_renewer_thread_started", extra={
            "agent_instance_id": self._agent_instance_id,
        })

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("lease_renewer_thread_stopped", extra={
            "agent_instance_id": self._agent_instance_id,
        })

    def _renewal_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._jobs_lock:
                token_jobs = list(self._fencing_tokens.keys())

            for job_id in token_jobs:
                if self._stop_event.is_set():
                    break
                try:
                    self._extend_lock(job_id)
                except Exception as e:
                    logger.warning("lease_renewal_tick_failed", extra={
                        "agent_instance_id": self._agent_instance_id,
                        "job_id": job_id,
                        "error": str(e),
                    })

            self._stop_event.wait(self._renewal_interval)

    def set_fencing_token(self, job_id: int, token: str, device_id: Optional[int] = None) -> None:
        """Store fencing_token and device_id for a job.

        ADR-0019 Phase 2b: token storage.
        Phase 3b: added device_id tracking.
        """
        with self._jobs_lock:
            self._fencing_tokens[job_id] = token
            if device_id is not None:
                self._device_ids[job_id] = device_id
            else:
                self._device_ids.pop(job_id, None)  # 防 job_id 异常复用时残留旧 device_id

    def clear_fencing_token(self, job_id: int) -> Optional[int]:
        """Remove fencing_token and device_id for a job. Returns the device_id if any.

        ADR-0019 Phase 2b: token removal.
        Phase 3b: returns device_id for clean _active_device_ids removal.
        """
        with self._jobs_lock:
            self._fencing_tokens.pop(job_id, None)
            return self._device_ids.pop(job_id, None)

    def _extend_lock(self, job_id: int) -> None:
        with self._jobs_lock:
            token = self._fencing_tokens.get(job_id)
        if not token:
            # 必须保留：renewal loop snapshot 后 token 可能被并发清理
            logger.debug("extend_lock_skipped_no_token", extra={
                "agent_instance_id": self._agent_instance_id,
                "job_id": job_id,
            })
            return

        url = f"{self._api_url}/api/v1/agent/jobs/{job_id}/extend_lock"
        headers = {"X-Agent-Secret": self._agent_secret} if self._agent_secret else {}
        last_error = None

        for attempt in range(1, self._post_retries + 1):
            try:
                resp = requests.post(
                    url, json={"fencing_token": token}, headers=headers, timeout=10,
                )
                resp.raise_for_status()
                result = resp.json()
                logger.info("lease_extended", extra={
                    "agent_instance_id": self._agent_instance_id,
                    "job_id": job_id,
                    "expires_at": result.get("expires_at"),
                })
                return
            except requests.HTTPError as e:
                last_error = e
                status = e.response.status_code if e.response is not None else None
                if status in (409, 404):
                    # 409: token rejected; 404: job not found — lease definitively lost
                    with self._jobs_lock:
                        self._job_ids.discard(job_id)
                        self._fencing_tokens.pop(job_id, None)
                        device_id = self._device_ids.pop(job_id, None)
                    if self._on_lease_lost:
                        self._on_lease_lost(job_id, device_id)
                    logger.warning(
                        "lease_lost_409" if status == 409 else "lease_lost_404",
                        extra={
                            "agent_instance_id": self._agent_instance_id,
                            "job_id": job_id,
                            "status_code": status,
                            "reason": (
                                "backend_rejected_token" if status == 409
                                else "job_not_found_on_backend"
                            ),
                        },
                    )
                    return
                logger.warning("lease_extend_http_error", extra={
                    "agent_instance_id": self._agent_instance_id,
                    "job_id": job_id,
                    "attempt": attempt,
                    "status_code": status or "unknown",
                    "error": str(e),
                })
            except requests.RequestException as e:
                last_error = e
                logger.warning("lease_extend_network_error", extra={
                    "agent_instance_id": self._agent_instance_id,
                    "job_id": job_id,
                    "attempt": attempt,
                    "error": str(e),
                })

            if attempt < self._post_retries:
                delay = self._post_retry_base_delay * (2 ** (attempt - 1))
                time.sleep(delay)

        # 所有重试耗尽 (non-409) — 不清理状态，网络恢复后下一 tick 继续续租
        logger.warning("lease_extend_all_retries_exhausted", extra={
            "agent_instance_id": self._agent_instance_id,
            "job_id": job_id,
            "attempts": self._post_retries,
            "last_error": str(last_error),
        })
