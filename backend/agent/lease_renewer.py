"""DeviceLease 续租器 —— 后台线程定期续期活跃 Job 的 device lease。

提取自 backend.agent.main，供 main.py barrel re-export。

ADR-0019 Phase 3b: LockRenewalManager 重命名为 LeaseRenewer，补齐 409 清理闭环，
统一结构化日志，明确 renew_interval < lease_ttl/2 配置关系。
"""

import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Optional, Set

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
        host_id: str = "",
        coordinator: Any = None,  # ADR-0026 Step 5b: per-job execution_state snapshots
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
        self._local_worker_tokens: Dict[int, str] = {}
        self._device_ids: Dict[int, int] = {}      # Phase 3b: job_id → device_id
        self._agent_instance_id = agent_instance_id
        self._on_lease_lost = on_lease_lost
        self._host_id = host_id
        self._coordinator = coordinator  # ADR-0026 Step 5b
        # One batch request renews the whole host per tick (P0 scale reduction).
        # Chunked so a large host stays under the backend's per-request cap.
        self._batch_chunk = max(int(os.getenv("AGENT_LEASE_EXTEND_BATCH_CHUNK", "100")), 1)
        # Auto-fallback to the per-job endpoint when a backend predates
        # /leases/extend-batch (Agents hot-update independently of the control
        # plane, so a new Agent may talk to an old backend during rollout).
        self._batch_supported = True

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
                # ADR-0019 Phase 3b 审计 #4: fencing_token 与 _active_job_ids 一致性 watchdog。
                # Why: 异常路径若漏调 _deregister_active_job,token 会残留导致死租约持续续期。
                # How to apply: 每个 tick 开始前扫描差集,主动清理孤立 token。
                orphan_tokens = [
                    jid for jid in self._fencing_tokens
                    if jid not in self._job_ids
                ]
                for jid in orphan_tokens:
                    device_id = self._device_ids.pop(jid, None)
                    self._fencing_tokens.pop(jid, None)
                    self._local_worker_tokens.pop(jid, None)
                    logger.warning("lease_renewer_orphan_token_purged", extra={
                        "agent_instance_id": self._agent_instance_id,
                        "job_id": jid,
                        "device_id": device_id,
                        "reason": "token_held_without_active_job_id",
                    })
                token_jobs = list(self._fencing_tokens.keys())

            if not token_jobs:
                self._stop_event.wait(self._renewal_interval)
                continue

            if self._batch_supported and self._host_id:
                try:
                    self._extend_batch(token_jobs)
                except Exception as e:
                    logger.warning("lease_renewal_batch_tick_failed", extra={
                        "agent_instance_id": self._agent_instance_id,
                        "error": str(e),
                    })
            else:
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

    def set_fencing_token(
        self,
        job_id: int,
        token: str,
        device_id: Optional[int] = None,
        local_worker_token: str = "",
    ) -> None:
        """Store fencing_token and device_id for a job.

        ADR-0019 Phase 2b: token storage.
        Phase 3b: added device_id tracking.
        """
        effective_worker_token = local_worker_token or token
        with self._jobs_lock:
            self._fencing_tokens[job_id] = token
            self._local_worker_tokens[job_id] = effective_worker_token
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
            self._local_worker_tokens.pop(job_id, None)
            return self._device_ids.pop(job_id, None)

    def clear_fencing_token_if_current(
        self,
        job_id: int,
        token: str,
        local_worker_token: str = "",
    ) -> Optional[int]:
        """Remove token only when it still matches the current active token.

        Empty token means unconditional clear for explicit local cleanup paths
        such as control abort / submit failure before worker fully starts.
        """
        with self._jobs_lock:
            current = self._fencing_tokens.get(job_id)
            current_worker = self._local_worker_tokens.get(job_id, current or "")
            expected_worker = local_worker_token or token
            if token and current and current != token:
                return None
            if expected_worker and current_worker and current_worker != expected_worker:
                return None
            self._fencing_tokens.pop(job_id, None)
            self._local_worker_tokens.pop(job_id, None)
            return self._device_ids.pop(job_id, None)

    def _handle_lease_lost(self, job_id: int, token: str, *, reason: str, status_code) -> None:
        """Definitive lease loss for *job_id* (409 token rejected / 404 gone /
        batch stale_token / lease_missing / job_not_running).

        Shared by the per-job and batch renewal paths. The stale-token guard
        keeps a superseded worker's loss from tearing down a freshly re-claimed
        job that reused the same job_id under a rotated token.
        """
        with self._jobs_lock:
            current = self._fencing_tokens.get(job_id)
            if token and current and current != token:
                logger.info("lease_lost_stale_token_ignored", extra={
                    "agent_instance_id": self._agent_instance_id,
                    "job_id": job_id,
                    "status_code": status_code,
                })
                return
            self._job_ids.discard(job_id)
            self._fencing_tokens.pop(job_id, None)
            self._local_worker_tokens.pop(job_id, None)
            device_id = self._device_ids.pop(job_id, None)
        if self._on_lease_lost:
            self._on_lease_lost(job_id, device_id)
        logger.warning("lease_lost", extra={
            "agent_instance_id": self._agent_instance_id,
            "job_id": job_id,
            "status_code": status_code,
            "reason": reason,
        })

    # Batch outcomes that mean "this Agent no longer owns the job" — mirror the
    # per-job 409/404 teardown. ``lease_missing`` is included: a running job with
    # no ACTIVE lease has been reconciled away and must not keep renewing.
    _BATCH_LOST_STATUSES = {"stale_token", "job_not_running", "lease_missing"}

    def _extend_batch(self, job_ids: list) -> None:
        """Renew all of this host's leases in one request (chunked).

        Snapshots each job's token under the lock so the request reflects the
        token the loop intended to renew; per-item results are dispatched to
        ``_handle_lease_lost`` (which re-checks the token before teardown, so a
        concurrent re-claim is not clobbered). Network/5xx errors leave state
        intact — the next tick retries, exactly like the per-job path.
        """
        url = f"{self._api_url}/api/v1/agent/leases/extend-batch"
        headers = {"X-Agent-Secret": self._agent_secret} if self._agent_secret else {}

        for start in range(0, len(job_ids), self._batch_chunk):
            if self._stop_event.is_set():
                return
            chunk = job_ids[start:start + self._batch_chunk]
            with self._jobs_lock:
                leases = [
                    {"job_id": jid, "fencing_token": self._fencing_tokens[jid]}
                    for jid in chunk
                    if jid in self._fencing_tokens
                ]
            if not leases:
                continue
            # ADR-0026 Step 5b: enrich with per-job signals from coordinator
            if self._coordinator is not None:
                for item in leases:
                    jv = self._coordinator.register_job(item["job_id"])
                    snap = jv.snapshot()
                    if snap.get("execution_state"):
                        item["execution_state"] = snap["execution_state"]
                        item["progress_marker"] = {}
                        if snap.get("last_progress_at"):
                            item["progress_marker"]["last_progress_at"] = snap[
                                "last_progress_at"
                            ]
            sent_tokens = {item["job_id"]: item["fencing_token"] for item in leases}

            try:
                resp = requests.post(
                    url,
                    json={
                        "host_id": self._host_id,
                        "agent_instance_id": self._agent_instance_id,
                        "leases": leases,
                    },
                    headers=headers,
                    timeout=15,
                )
                if resp.status_code in (404, 405):
                    # Backend predates the batch endpoint — fall back permanently
                    # this process, retry these jobs via the per-job path now.
                    self._batch_supported = False
                    logger.warning("lease_extend_batch_unsupported_fallback", extra={
                        "agent_instance_id": self._agent_instance_id,
                        "status_code": resp.status_code,
                    })
                    for jid in chunk:
                        if self._stop_event.is_set():
                            return
                        self._extend_lock(jid)
                    return
                resp.raise_for_status()
                payload = resp.json()
            except Exception as e:
                logger.warning("lease_extend_batch_http_error", extra={
                    "agent_instance_id": self._agent_instance_id,
                    "chunk_size": len(leases),
                    "error": str(e),
                })
                continue  # leave state intact; next tick retries

            data = payload.get("data") if isinstance(payload, dict) else None
            results = (data or {}).get("results", []) if isinstance(data, dict) else []
            for item in results:
                jid = item.get("job_id")
                status = item.get("status")
                if jid is None:
                    continue
                if status == "renewed":
                    logger.info("lease_extended", extra={
                        "agent_instance_id": self._agent_instance_id,
                        "job_id": jid,
                        "expires_at": item.get("expires_at"),
                    })
                elif status in self._BATCH_LOST_STATUSES:
                    self._handle_lease_lost(
                        jid, sent_tokens.get(jid, ""),
                        reason=f"batch_{status}", status_code=status,
                    )

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
                        current = self._fencing_tokens.get(job_id)
                        if current and current != token:
                            logger.info(
                                "lease_lost_stale_token_ignored",
                                extra={
                                    "agent_instance_id": self._agent_instance_id,
                                    "job_id": job_id,
                                    "status_code": status,
                                },
                            )
                            return
                        self._job_ids.discard(job_id)
                        self._fencing_tokens.pop(job_id, None)
                        self._local_worker_tokens.pop(job_id, None)
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
