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


def test_repeated_submit_failures_do_not_erode_capacity(monkeypatch):
    """#2072：`pool.submit` 抛「非 shutdown」错误时配额必须归还。

    旧实现在 `raise` 前没有任何归还（`_run_and_release` 从未被调度），而
    `_queue_slots` 是模块级、跨 pool 重建存活 → 每失败一次就少一格容量，
    到 0 后所有后台提交恒抛 PoolQueueFullError，调用方（通知/后处理）
    的行为是「记一条 warning 后丢弃」——静默丢后台工作。
    """

    class _AlwaysBoom:
        def submit(self, _fn):
            raise RuntimeError("unrelated boom")

    monkeypatch.setattr(thread_pool, "_pool", _AlwaysBoom())
    for _ in range(thread_pool.MAX_QUEUE + 10):
        with pytest.raises(RuntimeError, match="unrelated boom"):
            thread_pool.submit(lambda: None)

    assert thread_pool.queue_depth() == 0, "失败提交不得留下占用配额的僵尸"

    # 容量完好：换回真池仍可提交并跑完（旧实现此刻恒 PoolQueueFullError）
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setattr(thread_pool, "_pool", ThreadPoolExecutor(max_workers=1))
    done = threading.Event()
    thread_pool.submit(done.set)
    assert done.wait(timeout=5)
    assert thread_pool.drain(timeout=5)


def test_shutdown_cancelled_futures_release_slots(monkeypatch):
    """#2072：被 `shutdown(cancel_futures=True)` 取消的排队任务也要归还配额。

    任务体不执行 → 旧实现挂在任务体 finally 上的归还永不发生；生产路径目前只在
    带 timeout 的优雅停机里出现，测试里则表现为同一进程内跨用例累积、偶发
    PoolQueueFullError 与 `drain()` 等不到零（conftest 清库前的排空守卫，#2074）。
    """
    from concurrent.futures import ThreadPoolExecutor

    pool = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(thread_pool, "_pool", pool)

    gate = threading.Event()

    def _blocker():
        gate.wait(timeout=10)

    running = thread_pool.submit(_blocker)        # 占住唯一 worker
    for _ in range(5):
        thread_pool.submit(lambda: None)          # 排队中的 5 条
    assert thread_pool.queue_depth() == 6

    pool.shutdown(wait=False, cancel_futures=True)
    gate.set()
    running.result(timeout=10)

    assert thread_pool.drain(timeout=10), "被取消的排队任务也必须归还配额"
    assert thread_pool.queue_depth() == 0
