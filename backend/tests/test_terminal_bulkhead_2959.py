"""ADR-0047 D2（#2959）：`/complete` 终态舱壁——名额、等待预算、拒绝不泄漏名额。

舱壁的判据不是「限流了多少」，而是三条形态：

1. 同时持有名额 ≤ `STP_TERMINAL_BULKHEAD_CONCURRENCY`（默认 16）；
2. 等待超过 `STP_TERMINAL_BULKHEAD_WAIT_MS`（默认 500ms）**立即拒绝**，不占着等；
3. 拒绝路径**不漏名额**——`wait_for` 取消 `semaphore.acquire()` 后计数必须还回去，
   否则一次过载波会把舱壁永久缩容（这正是 R523 里最贵的形态：故障自己放大自己）。

指标（inflight / waiting / rejected / wait_seconds）在这里一并钉住：它们是 D5 告警的
生产者，名字改了而告警没改就是「永不触发」那一类静默失效。
"""

from __future__ import annotations

import asyncio

import pytest

from backend.core import terminal_bulkhead as tb


@pytest.fixture(autouse=True)
def _fresh_bulkhead():
    tb._reset_for_tests()
    yield
    tb._reset_for_tests()


def _get(name: str, labels: dict | None = None) -> float:
    from prometheus_client import REGISTRY

    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


def test_limits_default_and_env(monkeypatch):
    monkeypatch.delenv("STP_TERMINAL_BULKHEAD_CONCURRENCY", raising=False)
    monkeypatch.delenv("STP_TERMINAL_BULKHEAD_WAIT_MS", raising=False)
    assert tb.limits() == (16, 0.5)

    monkeypatch.setenv("STP_TERMINAL_BULKHEAD_CONCURRENCY", "3")
    monkeypatch.setenv("STP_TERMINAL_BULKHEAD_WAIT_MS", "250")
    assert tb.limits() == (3, 0.25)


@pytest.mark.parametrize("raw", ["0", "-1", "abc", ""])
def test_limits_fall_back_on_bad_env(monkeypatch, raw):
    """误配不得退化成「零闸门」或「无限等待」。"""
    monkeypatch.setenv("STP_TERMINAL_BULKHEAD_CONCURRENCY", raw)
    monkeypatch.setenv("STP_TERMINAL_BULKHEAD_WAIT_MS", raw)
    assert tb.limits() == (16, 0.5)


async def test_admits_at_most_concurrency_at_a_time(monkeypatch):
    monkeypatch.setenv("STP_TERMINAL_BULKHEAD_CONCURRENCY", "2")
    monkeypatch.setenv("STP_TERMINAL_BULKHEAD_WAIT_MS", "2000")

    concurrent = 0
    peak = 0
    done = 0

    async def worker():
        nonlocal concurrent, peak, done
        async with tb.terminal_slot():
            concurrent += 1
            peak = max(peak, concurrent)
            await asyncio.sleep(0.02)
            concurrent -= 1
            done += 1

    await asyncio.gather(*(worker() for _ in range(6)))

    assert done == 6, "所有请求最终都要被放行（排队而不是丢弃）"
    assert peak == 2, f"同刻持有名额 {peak} ≠ 配置 2"
    assert _get("stability_terminal_bulkhead_rejected_total") == 0.0


async def test_wait_budget_rejects_and_does_not_leak_slot(monkeypatch):
    """超预算拒绝 + 拒绝后名额必须完好（否则舱壁会被过载波永久缩容）。"""
    monkeypatch.setenv("STP_TERMINAL_BULKHEAD_CONCURRENCY", "1")
    monkeypatch.setenv("STP_TERMINAL_BULKHEAD_WAIT_MS", "50")

    rejected_before = _get("stability_terminal_bulkhead_rejected_total")
    holder_release = asyncio.Event()
    holder_ready = asyncio.Event()

    async def holder():
        async with tb.terminal_slot():
            holder_ready.set()
            await holder_release.wait()

    holder_task = asyncio.create_task(holder())
    await asyncio.wait_for(holder_ready.wait(), timeout=1)

    with pytest.raises(tb.TerminalBulkheadFull):
        async with tb.terminal_slot():
            pytest.fail("超预算的请求不得进入临界区")

    assert _get("stability_terminal_bulkhead_rejected_total") == rejected_before + 1
    assert _get("stability_terminal_bulkhead_inflight") == 1.0, "拒绝不得改变在飞计数"

    holder_release.set()
    await asyncio.wait_for(holder_task, timeout=1)

    # 名额没漏：现在可以立刻拿到
    async with tb.terminal_slot():
        pass
    assert _get("stability_terminal_bulkhead_inflight") == 0.0


async def test_metrics_track_inflight_waiting_and_wait_time(monkeypatch):
    monkeypatch.setenv("STP_TERMINAL_BULKHEAD_CONCURRENCY", "1")
    monkeypatch.setenv("STP_TERMINAL_BULKHEAD_WAIT_MS", "2000")

    wait_count_before = _get("stability_terminal_bulkhead_wait_seconds_count")
    release = asyncio.Event()
    ready = asyncio.Event()

    async def holder():
        async with tb.terminal_slot():
            ready.set()
            await release.wait()

    async def waiter():
        async with tb.terminal_slot():
            pass

    holder_task = asyncio.create_task(holder())
    await asyncio.wait_for(ready.wait(), timeout=1)
    assert _get("stability_terminal_bulkhead_inflight") == 1.0

    waiter_task = asyncio.create_task(waiter())
    await asyncio.sleep(0.05)  # 让 waiter 真正进入排队
    assert _get("stability_terminal_bulkhead_waiting") == 1.0, "排队中必须有水位指标"

    release.set()
    await asyncio.wait_for(asyncio.gather(holder_task, waiter_task), timeout=2)

    assert _get("stability_terminal_bulkhead_wait_seconds_count") >= wait_count_before + 2
    assert _get("stability_terminal_bulkhead_waiting") == 0.0
    assert _get("stability_terminal_bulkhead_inflight") == 0.0


async def test_rejection_log_is_once_per_burst_not_once_per_process(monkeypatch, caplog):
    """日志粒度是**每个 burst 一行**（2026-09-23 裁决修正）。

    原实现是「进程生命周期一次」：第一次削峰之后，第二次事故完全没有起点日志。
    现在以静默窗（`_REJECTION_LOG_QUIET_SECONDS`）划分 burst——burst 内只记一行，
    过了静默窗再拒绝会重新记。
    """
    import logging

    monkeypatch.setenv("STP_TERMINAL_BULKHEAD_CONCURRENCY", "1")
    monkeypatch.setenv("STP_TERMINAL_BULKHEAD_WAIT_MS", "30")
    caplog.set_level(logging.WARNING, logger="backend.core.terminal_bulkhead")

    async def hold_and_reject() -> None:
        release = asyncio.Event()
        ready = asyncio.Event()

        async def holder():
            async with tb.terminal_slot():
                ready.set()
                await release.wait()

        holder_task = asyncio.create_task(holder())
        await asyncio.wait_for(ready.wait(), timeout=1)
        for _ in range(3):
            with pytest.raises(tb.TerminalBulkheadFull):
                async with tb.terminal_slot():
                    pytest.fail("超预算的请求不得进入临界区")
        release.set()
        await asyncio.wait_for(holder_task, timeout=1)

    await hold_and_reject()
    burst_one = [r for r in caplog.records if "burst_start" in r.getMessage()]
    assert len(burst_one) == 1, [r.getMessage() for r in caplog.records]

    # 静默窗已过：把上一轮起点推回 120s 前，下一次拒绝必须重新记一行
    import time as _time

    monkeypatch.setattr(tb, "_last_rejection_log_at", _time.monotonic() - 120)
    await hold_and_reject()
    burst_two = [r for r in caplog.records if "burst_start" in r.getMessage()]
    assert len(burst_two) == 2, "第二轮削峰必须留下自己的起点日志"

    # 指标不受日志粒度影响：6 次拒绝一个不少
    assert _get("stability_terminal_bulkhead_rejected_total") >= 6
