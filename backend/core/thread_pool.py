# -*- coding: utf-8 -*-
"""
Shared bounded thread pool for fire-and-forget background work.

All background tasks (post-completion, notifications, etc.) should use this
pool instead of spawning raw ``threading.Thread`` instances, so concurrency
stays bounded and predictable.
"""

import os
from concurrent.futures import ThreadPoolExecutor

MAX_WORKERS = int(os.getenv("BACKGROUND_POOL_SIZE", "8"))

_pool = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="bg-worker")


def submit(fn, *args, **kwargs):
    """Submit *fn* to the shared background thread pool."""
    return _pool.submit(fn, *args, **kwargs)


def shutdown(wait=True, timeout=None):
    """Shut down the pool (called on app shutdown).

    Args:
        wait: Whether to wait for in-flight tasks to finish.
        timeout: Max seconds to wait before cancelling remaining futures.
                 Only used when wait=True.
    """
    if wait and timeout is not None:
        # Wait up to `timeout` seconds, then force-cancel remaining work
        import concurrent.futures
        _pool.shutdown(wait=False, cancel_futures=False)
        # Give running tasks a grace period
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # Pool threads are still running; just sleep briefly
            time.sleep(0.2)
        _pool.shutdown(wait=False, cancel_futures=True)
    else:
        _pool.shutdown(wait=wait)
