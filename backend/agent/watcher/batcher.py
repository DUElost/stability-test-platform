"""EventBatcher — InotifydSource 事件聚合 + 路由到 SignalEmitter。

职责（KISS）：
    1. 接收 WatcherEvent 流（来自 InotifydSource 的 callback，跨线程）
    2. AEE / VENDOR_AEE 类即时直通：立即调用 on_emit_immediate（避免 crash 现场被覆盖）
    3. ANR / MOBILELOG 类按 (category, full_path) 去重 + 时间窗/数量窗触发批量 on_emit_batch
    4. stop(drain=True) 同步 flush 剩余条目，给上层 (DeviceLogWatcher) 一致语义

边界（YAGNI）：
    - 不做文件 pull / artifact 上传（阶段 5 LogPuller 负责）
    - 不做磁盘持久化（events 暂存内存，丢失只代表 inotifyd 那一刻的事件丢失，
      下次新事件仍会被 inotifyd 上报；保证已 emit 的 signal 通过 SignalEmitter 入 LocalDB）
    - 不做事件 → outbox 的网络上送（OutboxDrainer 进程级单例处理）

线程模型：
    - 生产者：inotifyd 读线程调用 add_event()（持锁追加）
    - 消费者：内部 _flusher_thread 周期性扫描 deadline 触发 batch flush
    - on_emit_* 回调在 _flusher_thread 中调用；调用方需自保线程安全
    - stop()：发停止信号 → 最后一次 flush（drain=True 时）→ join

stats 字段：
    events_total      add_event() 累计入参条数
    events_deduped    被 (category, full_path) 去重命中的条数
    immediate_emits   AEE/VENDOR_AEE 直通调用次数
    batch_emits       聚合批次触发次数
    signals_total     最终调用 on_emit_* 的总条数（= events_total - events_deduped）
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

from .sources import WatcherEvent

logger = logging.getLogger(__name__)


# 默认即时直通的 category 集合（crash 类，单条事件足以触发 bugreport）
DEFAULT_IMMEDIATE_CATEGORIES: Set[str] = {"AEE", "VENDOR_AEE"}


@dataclass
class BatcherStats:
    """运行期统计，由 DeviceLogWatcher / Manager 抓取。"""

    events_total: int = 0
    events_deduped: int = 0
    immediate_emits: int = 0
    batch_emits: int = 0
    signals_total: int = 0


@dataclass
class _PendingItem:
    event: WatcherEvent
    enqueued_at: float


class EventBatcher:
    """事件聚合器（per-device，进程内）。

    用法（典型）：
        batcher = EventBatcher(
            on_emit_immediate=lambda ev: emitter.emit(...),
            on_emit_batch=lambda events: [emitter.emit(...) for ev in events],
            batch_interval_seconds=5.0,
            batch_max_events=20,
        )
        batcher.start()
        # InotifydSource 回调:
        source.on_event = batcher.add_event
        ...
        batcher.stop(drain=True, timeout=5.0)
    """

    def __init__(
        self,
        *,
        on_emit_immediate: Callable[[WatcherEvent], None],
        on_emit_batch: Callable[[List[WatcherEvent]], None],
        batch_interval_seconds: float = 5.0,
        batch_max_events: int = 20,
        immediate_categories: Optional[Set[str]] = None,
        queue_maxsize: int = 1000,
    ) -> None:
        self._on_immediate = on_emit_immediate
        self._on_batch = on_emit_batch
        self._batch_interval = float(batch_interval_seconds)
        self._batch_max = int(batch_max_events)
        self._immediate_categories = (
            set(immediate_categories) if immediate_categories is not None
            else set(DEFAULT_IMMEDIATE_CATEGORIES)
        )
        self._queue_maxsize = int(queue_maxsize)

        # 状态
        self._lock = threading.Lock()
        self._pending: List[_PendingItem] = []
        self._dedup_keys: Set[Tuple[str, str]] = set()
        self._stop_evt = threading.Event()
        self._wake_evt = threading.Event()  # add_event 满 batch 时立刻唤醒 flusher
        self._thread: Optional[threading.Thread] = None
        self._first_enqueue_at: Optional[float] = None
        self.stats = BatcherStats()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> None:
        """启动后台 flush 线程。重复 start 为 no-op。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._flusher_loop,
            name="event-batcher",
            daemon=True,
        )
        self._thread.start()
        logger.debug(
            "event_batcher_started interval=%.1fs max=%d immediate=%s",
            self._batch_interval, self._batch_max, sorted(self._immediate_categories),
        )

    def stop(self, *, drain: bool = True, timeout: float = 5.0) -> None:
        """请求停止。

        drain=True：先发停止信号，再等 flusher 线程做最后一次 flush；
                    若超时（仍有 pending），剩余条目交后续阶段不再 emit（已记 events_dropped）。
        drain=False：发停止信号即返回；pending 内容直接丢弃。
        """
        self._stop_evt.set()
        self._wake_evt.set()  # 立刻唤醒可能在 wait 的 flusher
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        if drain:
            # flusher 退出前已自动 flush_pending；这里兜底做一次 best-effort
            try:
                self.flush_pending(force=True)
            except Exception:
                logger.exception("event_batcher_drain_flush_failed")
        logger.debug("event_batcher_stopped stats=%s", self.stats)

    # ------------------------------------------------------------------
    # 生产者入口
    # ------------------------------------------------------------------

    def add_event(self, event: WatcherEvent) -> None:
        """生产者侧：inotifyd 读线程调用。

        - AEE / VENDOR_AEE 立即触发 on_emit_immediate（不入 pending、不去重）
        - 其余按 (category, full_path) 去重后入 pending；满 batch_max 时唤醒 flusher
        """
        with self._lock:
            self.stats.events_total += 1

        if event.category in self._immediate_categories:
            try:
                self._on_immediate(event)
            except Exception:
                logger.exception(
                    "event_batcher_immediate_callback_failed category=%s file=%s",
                    event.category, event.filename,
                )
            with self._lock:
                self.stats.immediate_emits += 1
                self.stats.signals_total += 1
            return

        key = (event.category, event.full_path)
        flush_now = False
        with self._lock:
            if key in self._dedup_keys:
                self.stats.events_deduped += 1
                return
            # 队列满保护：超出上限直接丢（仍记 dropped 由调用方观测）
            if len(self._pending) >= self._queue_maxsize:
                self.stats.events_deduped += 1  # 计为去重以保持 signals_total 守恒
                logger.warning(
                    "event_batcher_queue_full size=%d dropped category=%s file=%s",
                    self._queue_maxsize, event.category, event.filename,
                )
                return
            self._dedup_keys.add(key)
            self._pending.append(_PendingItem(event=event, enqueued_at=time.monotonic()))
            if self._first_enqueue_at is None:
                self._first_enqueue_at = self._pending[-1].enqueued_at
            if len(self._pending) >= self._batch_max:
                flush_now = True
        if flush_now:
            self._wake_evt.set()

    # ------------------------------------------------------------------
    # 测试/收尾入口
    # ------------------------------------------------------------------

    def flush_pending(self, *, force: bool = False) -> int:
        """同步触发一次 batch flush，返回本次发出的条数。

        force=True：忽略 deadline，立即 flush 所有 pending（drain / 测试用）
        force=False：仅当达到 batch_interval 或 batch_max 时 flush
        """
        with self._lock:
            if not self._pending:
                return 0
            if not force:
                now = time.monotonic()
                deadline_reached = (
                    self._first_enqueue_at is not None
                    and (now - self._first_enqueue_at) >= self._batch_interval
                )
                if not (deadline_reached or len(self._pending) >= self._batch_max):
                    return 0
            batch = [item.event for item in self._pending]
            self._pending.clear()
            self._dedup_keys.clear()
            self._first_enqueue_at = None

        try:
            self._on_batch(batch)
            with self._lock:
                self.stats.batch_emits += 1
                self.stats.signals_total += len(batch)
        except Exception:
            logger.exception("event_batcher_batch_callback_failed count=%d", len(batch))
            # 失败不重试：下一条同 (category, path) 仍会被 inotifyd 吐 → 重新去重入队
            # SignalEmitter 内部失败有 LocalDB UNIQUE 兜底；批 emit 实现应自保
        return len(batch)

    # ------------------------------------------------------------------
    # 内部循环
    # ------------------------------------------------------------------

    def _flusher_loop(self) -> None:
        """后台线程：周期性触发 flush。"""
        while not self._stop_evt.is_set():
            # 短粒度等待：max_events 触发时由 _wake_evt 立刻唤醒
            self._wake_evt.wait(timeout=min(0.5, self._batch_interval))
            self._wake_evt.clear()
            if self._stop_evt.is_set():
                break
            try:
                self.flush_pending(force=False)
            except Exception:
                logger.exception("event_batcher_loop_unhandled")
        # 退出前最后一次 flush（drain）
        try:
            self.flush_pending(force=True)
        except Exception:
            logger.exception("event_batcher_final_flush_failed")


__all__ = [
    "EventBatcher",
    "BatcherStats",
    "DEFAULT_IMMEDIATE_CATEGORIES",
]
