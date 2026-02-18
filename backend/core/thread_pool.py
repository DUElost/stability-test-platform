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


def shutdown(wait=True):
    """Shut down the pool (called on app shutdown)."""
    _pool.shutdown(wait=wait)
