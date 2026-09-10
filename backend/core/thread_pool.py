# -*- coding: utf-8 -*-
"""
Shared bounded thread pool for fire-and-forget background work.

All background tasks (post-completion, notifications, etc.) should use this
pool instead of spawning raw ``threading.Thread`` instances, so concurrency
stays bounded and predictable.

#1122：**待提交队列也有界** —— ThreadPoolExecutor 自带队列无界，网络停滞时
（如 SMTP 挂死）任务积压会无限增长。提交路径用信号量限流：满了抛
``PoolQueueFullError``，由调用方决定丢弃/告警；拒绝计数与队列深度经
``snapshot()`` 与 Prometheus 指标可观测。
"""

import os
import threading
from concurrent.futures import ThreadPoolExecutor

MAX_WORKERS = int(os.getenv("BACKGROUND_POOL_SIZE", "8"))
# #1122：待提交队列上限（含在途）。满了即拒绝，绝不静默积压。
MAX_QUEUE = int(os.getenv("BACKGROUND_POOL_MAX_QUEUE", "200"))

_pool_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="bg-worker")

# 信号量独立于 _pool 存活：shutdown 重建 pool 不清空配额（在途任务结束后仍要释放）。
_queue_slots = threading.BoundedSemaphore(MAX_QUEUE)
_slots_lock = threading.Lock()
_depth = 0
_rejected_total = 0
_submitted_total = 0


class PoolQueueFullError(RuntimeError):
    """后台线程池待提交队列已满（#1122：有界队列的拒绝信号）。"""


def _new_pool() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="bg-worker")


def queue_depth() -> int:
    """当前占用配额的任务数（在途 + 排队），观测用。"""
    with _slots_lock:
        return _depth


def snapshot() -> dict:
    """线程池水位快照（观测用）：容量 / 深度 / 累计提交与拒绝。"""
    return {
        "max_workers": MAX_WORKERS,
        "max_queue": MAX_QUEUE,
        "queue_depth": queue_depth(),
        "submitted_total": _submitted_total,
        "rejected_total": _rejected_total,
    }


def _record_rejected() -> None:
    global _rejected_total
    with _slots_lock:
        _rejected_total += 1
    _record_rejected_metric()
    _set_metrics_gauges()


def _set_metrics_gauges() -> None:
    """把队列深度同步到 Prometheus（best-effort；无 prometheus 环境安全 no-op）。"""
    try:
        from backend.core.metrics import background_pool_queue_depth

        background_pool_queue_depth.set(queue_depth())
    except Exception:
        pass


def submit(fn, *args, **kwargs):
    """Submit *fn* to the shared background thread pool.

    #1122：队列有界 —— 满了抛 :class:`PoolQueueFullError`（绝不静默积压），
    配额在任务结束后自动归还。
    """
    global _submitted_total, _depth, _pool
    if not _queue_slots.acquire(blocking=False):
        _record_rejected()
        raise PoolQueueFullError(
            f"background pool queue full (max_queue={MAX_QUEUE})"
        )
    with _slots_lock:
        _submitted_total += 1
        _depth += 1
    _set_metrics_gauges()

    def _run_and_release():
        global _depth
        try:
            fn(*args, **kwargs)
        finally:
            _queue_slots.release()
            with _slots_lock:
                _depth -= 1
            _set_metrics_gauges()

    with _pool_lock:
        if _pool is None:
            _pool = _new_pool()
        pool = _pool

    try:
        return pool.submit(_run_and_release)
    except RuntimeError as exc:
        # 测试环境中 TestClient 触发 shutdown 后，允许自动重建线程池
        if "cannot schedule new futures after shutdown" not in str(exc):
            raise
        with _pool_lock:
            if _pool is pool:
                _pool = _new_pool()
            pool = _pool
        return pool.submit(_run_and_release)


def _record_rejected_metric() -> None:
    try:
        from backend.core.metrics import background_pool_rejected_total

        background_pool_rejected_total.inc()
    except Exception:
        pass


def shutdown(wait=True, timeout=None):
    """Shut down the pool (called on app shutdown).

    Args:
        wait: Whether to wait for in-flight tasks to finish.
        timeout: Max seconds to wait before cancelling remaining futures.
                 Only used when wait=True.
    """
    global _pool
    with _pool_lock:
        pool = _pool
        if pool is None:
            return
        _pool = None

    import sys
    if wait and timeout is not None:
        # Wait up to `timeout` seconds, then force-cancel remaining work
        # cancel_futures is available in Python 3.9+
        if sys.version_info >= (3, 9):
            pool.shutdown(wait=False, cancel_futures=False)
        else:
            pool.shutdown(wait=False)
        # Give running tasks a grace period
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # Pool threads are still running; just sleep briefly
            time.sleep(0.2)
        if sys.version_info >= (3, 9):
            pool.shutdown(wait=False, cancel_futures=True)
        else:
            pool.shutdown(wait=False)
    else:
        pool.shutdown(wait=wait)
