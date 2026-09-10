"""#1122 —— 后台线程池待提交队列有界 + 可观测。"""
from __future__ import annotations

import threading
import time

import pytest

from backend.core import thread_pool


@pytest.fixture(autouse=True)
def _drain_pool():
    """每条用例前后等池清空，避免全局水位互相污染。"""
    def _wait_empty(timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while thread_pool.queue_depth() > 0 and time.monotonic() < deadline:
            time.sleep(0.02)

    _wait_empty()
    yield
    _wait_empty()


def test_submit_runs_fn_and_releases_slot():
    done = threading.Event()

    thread_pool.submit(done.set)

    assert done.wait(timeout=5)
    assert thread_pool.queue_depth() == 0
    snap = thread_pool.snapshot()
    assert snap["submitted_total"] >= 1


def test_submit_releases_slot_when_fn_raises():
    started = threading.Event()
    proceed = threading.Event()

    def boom():
        started.set()
        proceed.wait(timeout=5)
        raise ValueError("task failed")

    thread_pool.submit(boom)
    assert started.wait(timeout=5)
    depth_inflight = thread_pool.queue_depth()
    assert depth_inflight >= 1

    proceed.set()
    deadline = time.monotonic() + 5
    while thread_pool.queue_depth() > 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert thread_pool.queue_depth() == 0, "fn 抛异常也必须归还配额"

    # 配额已释放：再次提交正常
    done = threading.Event()
    thread_pool.submit(done.set)
    assert done.wait(timeout=5)


def test_queue_full_rejects_and_counts():
    """队列满 → PoolQueueFullError + rejected_total 递增，绝不静默积压。"""
    rejected_before = thread_pool.snapshot()["rejected_total"]

    gated = []
    try:
        for _ in range(thread_pool.MAX_QUEUE):
            ev = threading.Event()
            thread_pool.submit(ev.wait, 10)
            gated.append(ev)
        deadline = time.monotonic() + 5
        while thread_pool.queue_depth() < thread_pool.MAX_QUEUE and time.monotonic() < deadline:
            time.sleep(0.02)

        with pytest.raises(thread_pool.PoolQueueFullError):
            thread_pool.submit(lambda: None)
        assert thread_pool.snapshot()["rejected_total"] == rejected_before + 1
    finally:
        for ev in gated:
            ev.set()

    deadline = time.monotonic() + 5
    while thread_pool.queue_depth() > 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    # 释放后配额归还，可再次提交
    done = threading.Event()
    thread_pool.submit(done.set)
    assert done.wait(timeout=5)
