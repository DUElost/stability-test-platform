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
import time
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


def drain(timeout: float = 10.0, interval: float = 0.02) -> bool:
    """有界等待「在途 + 排队」任务清零（#2074）。

    清库前（backend/tests conftest 的 TRUNCATE）用它保证 fire-and-forget 任务
    （通知降级直达、post_completion 缓存刷新——都自开 ``SessionLocal`` 短事务）
    已全部落地，不再横跨到下一个用例的清库事务与之成环；优雅停机前的在飞
    工作判定是同一条原语。轮询 ``queue_depth()``，timeout 内排空返回 True，
    超时返回 False（调用方自行决定：测试侧照常继续，残余泄漏由清库的死锁
    现场取证直接现形）。
    """
    deadline = time.monotonic() + timeout
    while queue_depth() > 0:
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)
    return True


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
    配额在任务**终结**时自动归还。

    #2072：归还点挂在 future 的 done 回调上，而不是只挂在任务体的 ``finally``。
    任务体有不执行的两条路径——``pool.submit`` 抛错（根本没排上队）、以及
    ``shutdown(cancel_futures=True)`` 取消排队中的 future——旧实现这两条都不归还，
    而 ``_queue_slots`` 是模块级、跨 pool 重建存活，于是容量单调下降直至所有后台
    提交恒抛 ``PoolQueueFullError``（调用方记 warning 后丢弃 = 静默丢后台工作），
    ``drain()`` 也再也等不到 ``queue_depth()`` 归零。
    不变量：**离开本函数时，配额要么已交给 future，要么已归还**，两者恰有其一。
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

    def _release() -> None:
        global _depth
        with _slots_lock:
            _depth -= 1
        _set_metrics_gauges()
        _queue_slots.release()

    def _runner() -> None:
        fn(*args, **kwargs)

    with _pool_lock:
        if _pool is None:
            _pool = _new_pool()
        pool = _pool

    handed_off = False
    last_exc: RuntimeError | None = None
    try:
        # 至多两次尝试：第二次仅在 pool 已被 shutdown（测试环境 TestClient 常见）
        # 时重建后重试；其它 RuntimeError 原样上抛。
        for _ in range(2):
            try:
                fut = pool.submit(_runner)
            except RuntimeError as exc:
                if "cannot schedule new futures after shutdown" not in str(exc):
                    raise
                last_exc = exc
                with _pool_lock:
                    if _pool is pool:
                        _pool = _new_pool()
                    pool = _pool
                continue
            # 正常/异常/被取消都会触发 done 回调（3.9+ 语义，实测 3.13 成立）
            fut.add_done_callback(lambda _f: _release())
            handed_off = True
            return fut
        if last_exc is not None:  # 两次都被 shutdown 拒绝：原样抛出该错误
            raise last_exc
    finally:
        if not handed_off:
            # 未交给 future 的任何退出路径（抛错、两次被拒）在此归还；
            # 此时 done 回调必然尚未挂载，不会二次归还。
            _release()


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
