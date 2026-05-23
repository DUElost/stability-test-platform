"""Background thread that uploads un-acked step_trace records to the server via HTTP.

Design mirrors OutboxDrainThread: daemon thread, start()/stop()/drain_sync() API,
_stop_event.wait(interval) loop.

审计 Agent #6: 在 Phase A/B 主路径之外增加退避 + 死信。
- 普通失败 → ``bump_step_trace_attempt`` + 指数退避
- 达到 ``_MAX_ATTEMPTS`` → ``mark_step_trace_dead_letter``,行不再被取出
- 监控指标(``uploaded_total`` / ``failed_total`` / ``dead_letter_total``)暴露给 heartbeat

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
    # 审计 Agent #6: 持续失败超过此值的 trace 进入死信,不再阻塞 buffer。
    _MAX_ATTEMPTS = 10
    # 网络/5xx 失败时进入指数退避,最长 5 分钟一次。
    _BACKOFF_MAX = 300.0
    # #10: prune 调度 — 与 emitter / outbox_drainer 同套路防 SQLite 膨胀。
    # interval=5s × 12 ticks ≈ 60s 一次 prune;keep_recent=2000 ≈ 长跑 Agent 几小时的 trace 量。
    # 死信行不动(LocalDB.prune_acked_step_traces WHERE dead_letter = 0)。
    _PRUNE_EVERY_N_TICKS = 12
    _PRUNE_KEEP_RECENT = 2000

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
        # 审计 Agent #6: 当前轮次的退避时长;0 表示正常 interval。
        self._current_backoff = 0.0
        # #10: prune 计数器
        self._ticks_since_prune = 0
        # 监控指标(累计自进程启动)
        self._uploaded_total = 0
        self._failed_total = 0
        self._dead_letter_total = 0
        self._pruned_total = 0
        self._metrics_lock = threading.Lock()

    # ── monitoring ─────────────────────────────────────────────────────

    @property
    def uploaded_total(self) -> int:
        return self._uploaded_total

    @property
    def failed_total(self) -> int:
        return self._failed_total

    @property
    def dead_letter_total(self) -> int:
        return self._dead_letter_total

    @property
    def current_backoff(self) -> float:
        return self._current_backoff

    @property
    def pruned_total(self) -> int:
        return self._pruned_total

    def snapshot_metrics(self) -> Dict[str, Any]:
        """Read all counters atomically. Used by heartbeat stats."""
        with self._metrics_lock:
            return {
                "uploaded_total": self._uploaded_total,
                "failed_total": self._failed_total,
                "dead_letter_total": self._dead_letter_total,
                "current_backoff": self._current_backoff,
                "pruned_total": self._pruned_total,
            }

    def _bump_metric(self, key: str, delta: int = 1) -> None:
        with self._metrics_lock:
            setattr(self, key, getattr(self, key) + delta)

    # ── lifecycle ──────────────────────────────────────────────────────

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

    # ── main loop ──────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            wait = self._current_backoff or self._interval
            self._stop_event.wait(wait)
            if self._stop_event.is_set():
                break
            try:
                n = self._upload_once()
                if n > 0:
                    # 成功上传 → 重置退避
                    self._current_backoff = 0.0
            except Exception:
                logger.exception("step_trace_upload_error")
                self._advance_backoff()
            # #10: prune 调度独立于上传成败 — 时间挂钩,不受退避/空批影响。
            self._maybe_prune()

    def _maybe_prune(self) -> None:
        self._ticks_since_prune += 1
        if self._ticks_since_prune < self._PRUNE_EVERY_N_TICKS:
            return
        self._ticks_since_prune = 0
        try:
            pruned = self._local_db.prune_acked_step_traces(
                keep_recent=self._PRUNE_KEEP_RECENT,
            )
        except Exception:
            logger.exception("step_trace_prune_failed")
            return
        if pruned:
            self._bump_metric("_pruned_total", pruned)
            logger.info(
                "step_trace_pruned deleted=%d kept=%d",
                pruned, self._PRUNE_KEEP_RECENT,
            )

    def _advance_backoff(self) -> None:
        """指数退避:失败后下一次 wait 翻倍,封顶 ``_BACKOFF_MAX``。"""
        next_backoff = max(self._current_backoff, self._interval) * 2
        self._current_backoff = min(next_backoff, self._BACKOFF_MAX)
        logger.warning(
            "step_trace_upload_backoff next_wait=%.1fs",
            self._current_backoff,
        )

    # ── upload paths ───────────────────────────────────────────────────

    def _upload_once(self) -> int:
        traces = self._local_db.get_unacked_traces(after_id=self._last_id)
        if not traces:
            return 0
        batch = traces[: self._BATCH_LIMIT]
        headers: Dict[str, str] = {}
        if self._agent_secret:
            headers["X-Agent-Secret"] = self._agent_secret
        try:
            resp = requests.post(
                f"{self._api_url}/api/v1/agent/steps",
                json=[_to_payload(t) for t in batch],
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
        except requests.HTTPError as exc:
            status = _status_code(exc)
            if status in (404, 409):
                return self._resolve_rejected_batch(batch, headers)
            # 5xx 或其他 HTTP 错误 → 累计 attempts,超阈值进死信,再上抛触发 _loop 退避
            self._bump_or_dead_letter_batch(batch, f"http_{status}: {exc}")
            raise
        except requests.RequestException as exc:
            # 网络层错误同上
            self._bump_or_dead_letter_batch(batch, f"network: {exc}")
            raise
        for t in batch:
            self._local_db.mark_acked(t["id"])
            self._last_id = max(self._last_id, t["id"])
        self._bump_metric("_uploaded_total", len(batch))
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
        rejected_remaining: List[Dict[str, Any]] = []
        broke_at_idx: Optional[int] = None
        for idx, trace in enumerate(batch):
            try:
                resp = requests.post(
                    f"{self._api_url}/api/v1/agent/steps",
                    json=[_to_payload(trace)],
                    headers=headers,
                    timeout=15,
                )
                resp.raise_for_status()
            except requests.HTTPError as exc:
                status = _status_code(exc)
                if status in (404, 409):
                    self._ack_rejected_trace(trace)
                    resolved += 1
                    continue
                all_resolved = False
                broke_at_idx = idx
                logger.warning(
                    "step_trace_upload_retry trace_id=%s status=%s",
                    trace.get("id"), status,
                )
                rejected_remaining = batch[idx:]
                break
            except Exception as exc:
                all_resolved = False
                broke_at_idx = idx
                logger.warning(
                    "step_trace_upload_retry trace_id=%s error=%s",
                    trace.get("id"), exc,
                )
                rejected_remaining = batch[idx:]
                break

            self._local_db.mark_acked(trace["id"])
            self._bump_metric("_uploaded_total", 1)
            resolved += 1

        if all_resolved:
            self._last_id = max(self._last_id, max(t["id"] for t in batch))
        elif rejected_remaining:
            # 审计 Agent #6: 剩余未处理 trace 累计 attempts;超阈值进死信,避免下轮反复重试。
            self._bump_or_dead_letter_batch(
                rejected_remaining,
                f"resolve_break_at_idx_{broke_at_idx}",
            )
        logger.info("step_trace_upload_resolved count=%d/%d", resolved, len(batch))
        return resolved

    def _bump_or_dead_letter_batch(
        self, batch: List[Dict[str, Any]], error: str,
    ) -> None:
        """累计 attempts;超 ``_MAX_ATTEMPTS`` 进死信(标记 dead_letter=1)。"""
        for trace in batch:
            new_attempts = self._local_db.bump_step_trace_attempt(trace["id"], error)
            self._bump_metric("_failed_total", 1)
            if new_attempts >= self._MAX_ATTEMPTS:
                self._local_db.mark_step_trace_dead_letter(trace["id"], error)
                self._bump_metric("_dead_letter_total", 1)
                logger.warning(
                    "step_trace_dead_letter trace_id=%s job_id=%s attempts=%d error=%s",
                    trace["id"], trace.get("job_id"), new_attempts, error[:200],
                )

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
