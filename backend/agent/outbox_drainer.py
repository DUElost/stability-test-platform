"""终态 Outbox Drain 后台线程 —— 重试未确认的终端状态上报。

提取自 backend.agent.main，供 main.py barrel re-export。
"""

import logging
import os
import threading
import time
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)


class OutboxDrainThread:
    """Background thread that retries un-acked terminal-state payloads."""

    _ACKABLE_TERMINAL_STATUSES = {"COMPLETED", "FAILED", "ABORTED"}
    # #762/#742：与 log_signal/step_trace 死信上限同口径（各 10 次尝试后转死信）。
    _MAX_TERMINAL_ATTEMPTS = 10
    # #1551：HTTP 语义上**可重试**的状态码。408（Request Timeout）与 429
    # （Too Many Requests）属瞬时故障；被判成「中心永久拒绝」的代价不可逆——
    # job_terminal_outbox 是 terminal/log_signal/dle_register 三张同族表里
    # **唯一没有** replay_*_dead_letter 出口的，转死信后连人工都取不回来。
    # 429 在本平台确实可达：RateLimitMiddleware 已把 /api/v1/agent/jobs/ 移出
    # 豁免清单（300 req/min/IP），而终态上送端点正在该前缀下。
    _TRANSIENT_HTTP_STATUSES = frozenset({408, 429})
    # Retry-After 的退避上限——防御异常巨大的头部把某一行钉死在本进程里。
    _MAX_RETRY_AFTER_SECONDS = 300.0

    def __init__(self, api_url: str, local_db, interval: float = 15.0):
        self._api_url = api_url
        self._local_db = local_db
        self._interval = interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._agent_secret = os.getenv("AGENT_SECRET", "")
        # #1551：429 的 Retry-After 退避表（job_id → 可重试的单调时刻）。
        # 仅进程内生效，重启即忘——最坏退化为原先的「每 15s 重试一次」，不会更差。
        self._defer_until: Dict[int, float] = {}
        self._metrics_lock = threading.Lock()
        self._pending_backlog = 0
        self._flushed_total = 0
        self._conflicts_retained_total = 0
        self._unknown_retained_total = 0
        self._dead_letter_total = 0

    def snapshot_metrics(self) -> Dict[str, Any]:
        """Outbox backlog + flush counters for heartbeat / ops.

        口径提示（#762）：
        - ``conflicts_retained_total`` / ``unknown_retained_total`` 是**事件计数**
          （每 drain 循环每行 +1，15s 间隔），不是积压 gauge；
        - 「卡死行」的 distinct 口径看 ``dead_letter_total``（转死信数）与心跳
          上报的 ``terminal_outbox_dead_letter_total``（库内死信行数）。
        """
        with self._metrics_lock:
            return {
                "pending_backlog": self._pending_backlog,
                "flushed_total": self._flushed_total,
                "conflicts_retained_total": self._conflicts_retained_total,
                "unknown_retained_total": self._unknown_retained_total,
                "dead_letter_total": self._dead_letter_total,
            }

    def _set_pending_backlog(self, count: int) -> None:
        with self._metrics_lock:
            self._pending_backlog = count

    def _bump_flushed(self, delta: int) -> None:
        with self._metrics_lock:
            self._flushed_total += delta

    def _bump_retained_conflict(self, *, unknown: bool = False) -> None:
        with self._metrics_lock:
            self._conflicts_retained_total += 1
            if unknown:
                self._unknown_retained_total += 1

    def _retain_or_dead_letter(
        self, job_id: int, error: str, *, reason: str, unknown: bool = False,
    ) -> str:
        """retain 分支统一出口（#762/#742）：尝试数达上限转死信，否则留在 outbox。

        返回 ``"dead_letter"`` / ``"retained"``（便于调用方日志语义）。
        死信行不再被 ``get_pending_terminals`` 取出 → 不再占队头饿死新终态行。
        """
        attempts = self._local_db.bump_terminal_attempt(job_id, error)
        # MagicMock/旧 DB 兜底：非 int（无 attempts 语义）按原 retain 处理
        if (
            isinstance(attempts, int)
            and attempts >= self._MAX_TERMINAL_ATTEMPTS
            and hasattr(self._local_db, "mark_terminal_dead_letter")
        ):
            self._local_db.mark_terminal_dead_letter(job_id, error)
            with self._metrics_lock:
                self._dead_letter_total += 1
            logger.error(
                "outbox_drain_terminal_dead_letter job=%d attempts=%d reason=%s",
                job_id, attempts, reason,
            )
            return "dead_letter"
        self._bump_retained_conflict(unknown=unknown)
        return "retained"

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
        if hasattr(self._local_db, "count_pending_terminals"):
            self._set_pending_backlog(self._local_db.count_pending_terminals())
        pending = self._local_db.get_pending_terminals(limit=20)
        if not pending:
            self._local_db.prune_acked_terminals()
            return 0

        sent = 0
        headers = {"X-Agent-Secret": self._agent_secret} if self._agent_secret else {}
        for entry in pending:
            job_id = entry["job_id"]
            payload = entry["payload"]
            deferred_until = self._defer_until.get(job_id)
            if deferred_until is not None:
                if deferred_until > time.monotonic():
                    # #1551：中心在 429 上给了 Retry-After —— 按它退避，
                    # 别用自己的 15s 节奏把限流窗口一直续上。
                    continue
                self._defer_until.pop(job_id, None)
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
                # requests.Response.__bool__ is False for 4xx/5xx (alias of
                # ``ok``). Never use truthiness — #729 ghost /complete storm.
                status_code = (
                    e.response.status_code if e.response is not None else None
                )
                if status_code == 409:
                    error_code = self._parse_error_code(e.response)
                    current = self._parse_current_status(e.response)
                    if error_code == "TERMINAL_PAYLOAD_CONFLICT":
                        self._retain_or_dead_letter(
                            job_id, str(e), reason="terminal_payload_conflict",
                        )
                        logger.error(
                            "outbox_drain_terminal_payload_conflict job=%d",
                            job_id,
                        )
                    elif current is None:
                        # An unstructured 409 does not prove that the requested
                        # terminal fact is already durable. Retain it so
                        # recovery/sync can reconcile the fencing/state race.
                        self._retain_or_dead_letter(
                            job_id, str(e), reason="unstructured_409",
                        )
                        logger.warning(
                            "outbox_drain_conflict_retained job=%d current=unstructured",
                            job_id,
                        )
                    elif current in self._ACKABLE_TERMINAL_STATUSES:
                        self._local_db.ack_terminal(job_id)
                        sent += 1
                        logger.info(
                            "outbox_drain_conflict_ack job=%d current=%s (job is terminal)",
                            job_id, current,
                        )
                    else:
                        self._retain_or_dead_letter(
                            job_id, str(e),
                            reason=("current_unknown" if current == "UNKNOWN"
                                    else "current_non_ackable"),
                            unknown=current == "UNKNOWN",
                        )
                        logger.warning(
                            "outbox_drain_conflict_retained job=%d current=%s",
                            job_id, current,
                        )
                elif status_code == 404:
                    self._local_db.ack_terminal(job_id)
                    logger.warning("outbox_drain_job_gone job=%d", job_id)
                elif status_code in self._TRANSIENT_HTTP_STATUSES:
                    # #1551：408/429 按 HTTP 语义可重试 → 与 5xx 同口径，不判永久、
                    # 不进死信。429 额外按 Retry-After 退避（缺失/非法则沿用
                    # 默认节奏），免得自己的重试把限流窗口续上。
                    self._local_db.bump_terminal_attempt(job_id, str(e))
                    retry_after = self._parse_retry_after(e.response)
                    if retry_after > 0:
                        self._defer_until[job_id] = time.monotonic() + min(
                            retry_after, self._MAX_RETRY_AFTER_SECONDS,
                        )
                    logger.warning(
                        "outbox_drain_transient_retry job=%d status=%d retry_after=%.0fs",
                        job_id, status_code, retry_after,
                    )
                elif status_code is not None and 400 <= status_code < 500:
                    # #762：非 409/404 的 4xx 属中心永久拒绝 → 同走上限死信，
                    # 避免永久失败行占队头饿死新终态。
                    self._retain_or_dead_letter(
                        job_id, str(e), reason="http_%d" % status_code,
                    )
                else:
                    # #762：5xx / 无响应属瞬时故障，维持无限重试，不走死信上限
                    # （瞬时故障不得丢终态事实；与 4xx 永久拒绝有本质区别）。
                    self._local_db.bump_terminal_attempt(job_id, str(e))
            except Exception as e:
                # 网络异常同 5xx 口径：无限重试，不走死信上限。
                self._local_db.bump_terminal_attempt(job_id, str(e))
                logger.warning("outbox_drain_retry job=%d error=%s", job_id, e)

        if sent:
            self._bump_flushed(sent)
        if hasattr(self._local_db, "count_pending_terminals"):
            self._set_pending_backlog(self._local_db.count_pending_terminals())
        self._local_db.prune_acked_terminals()
        return sent

    @staticmethod
    def _parse_retry_after(response) -> float:
        """Retry-After 的秒数（#1551）。缺失/非法返回 0.0（沿用默认重试节奏）。

        只认 ``delta-seconds`` 形态；``HTTP-date`` 形态刻意忽略——本机时钟与中心
        可能不一致，按日期差算容易得到负数或超大值，反而不如按默认节奏。
        """
        if response is None:
            return 0.0
        try:
            raw = (response.headers.get("Retry-After") or "").strip()
        except Exception:
            return 0.0
        if not raw:
            return 0.0
        try:
            seconds = float(raw)
        except ValueError:
            return 0.0
        return seconds if seconds > 0 else 0.0

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

    @staticmethod
    def _parse_error_code(response) -> Optional[str]:
        try:
            body = response.json()
            detail = body.get("detail", {})
            if isinstance(detail, dict) and detail.get("code"):
                return str(detail["code"])
            error = body.get("error", {})
            if isinstance(error, dict) and error.get("code"):
                return str(error["code"])
        except Exception:
            pass
        return None
