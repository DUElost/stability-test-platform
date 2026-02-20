# -*- coding: utf-8 -*-
"""
Shared bounded thread pool for fire-and-forget background work.

All background tasks (post-completion, notifications, etc.) should use this
pool instead of spawning raw ``threading.Thread`` instances, so concurrency
stays bounded and predictable.
"""

import os
import threading
from concurrent.futures import ThreadPoolExecutor

MAX_WORKERS = int(os.getenv("BACKGROUND_POOL_SIZE", "8"))

_pool_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="bg-worker")


def _new_pool() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="bg-worker")


def submit(fn, *args, **kwargs):
    """Submit *fn* to the shared background thread pool."""
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = _new_pool()
        pool = _pool

    try:
        return pool.submit(fn, *args, **kwargs)
    except RuntimeError as exc:
        # 测试环境中 TestClient 触发 shutdown 后，允许自动重建线程池
        if "cannot schedule new futures after shutdown" not in str(exc):
            raise
        with _pool_lock:
            if _pool is pool:
                _pool = _new_pool()
            pool = _pool
        return pool.submit(fn, *args, **kwargs)


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

    if wait and timeout is not None:
        # Wait up to `timeout` seconds, then force-cancel remaining work
        import concurrent.futures
        pool.shutdown(wait=False, cancel_futures=False)
        # Give running tasks a grace period
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # Pool threads are still running; just sleep briefly
            time.sleep(0.2)
        pool.shutdown(wait=False, cancel_futures=True)
    else:
        pool.shutdown(wait=wait)
