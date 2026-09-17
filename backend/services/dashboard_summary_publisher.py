# -*- coding: utf-8 -*-
"""Coalesced dashboard-summary WS push (#2324 / ADR-0026 observation plane).

Heartbeat and other writers mark a dirty flag; a single flush ≤1Hz (default)
recomputes the summary and broadcasts ``dashboard_summary``.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from backend.core.database import SessionLocal
from backend.core.metrics import dashboard_summary_push_total
from backend.services.dashboard_summary import compute_dashboard_summary

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL = 1.0

# #2447：失败重试的退避上界与连续失败上限。失败分支此前直接调
# ``schedule_dashboard_summary_push()`` 重新武装——只要 DB/广播持续失败，就按正常
# 间隔（默认 1s）**永远重试**：每秒新开一个 SessionLocal、在事件循环里跑全量
# host/device 聚合、再打一条 exception 日志。改为指数退避，连续失败超过上限后
# **停到下一次真实变更**（正常入口会清零 streak）。
_MAX_RETRY_DELAY_SECONDS = 30.0
_MAX_RETRY_STREAK = 5

_loop: Optional[asyncio.AbstractEventLoop] = None
_dirty: bool = False
_flush_handle: Optional[asyncio.TimerHandle] = None
_flush_task: Optional[asyncio.Task] = None
_failure_streak: int = 0


def _push_interval_seconds() -> float:
    raw = os.getenv("STP_DASHBOARD_SUMMARY_PUSH_INTERVAL_SECONDS", str(_DEFAULT_INTERVAL))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_INTERVAL
    return value if value > 0 else _DEFAULT_INTERVAL


def bind_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Bind the ASGI event loop used for coalesced flush scheduling."""
    global _loop
    _loop = loop


def _reset_for_tests() -> None:
    """Clear publisher state between tests."""
    global _loop, _dirty, _flush_handle, _flush_task, _failure_streak
    if _flush_handle is not None:
        _flush_handle.cancel()
    if _flush_task is not None and not _flush_task.done():
        _flush_task.cancel()
    _loop = None
    _dirty = False
    _flush_handle = None
    _flush_task = None
    _failure_streak = 0


def _clear_flush_task(task: asyncio.Task) -> None:
    """flush 任务收尾后清引用（只在仍是当前任务时清，避免清掉后来者）。"""
    global _flush_task
    if _flush_task is task:
        _flush_task = None


def shutdown_dashboard_summary_publisher() -> None:
    """取消待执行的 flush（进程关闭序列用，#2447）。

    此前模块自带的 ``_reset_for_tests()`` 从未接入 lifespan 清理：关闭窗口里若恰好
    有一次 flush 已武装，它会在引擎/DB 正在关闭时触发，日志噪声之外没有任何收益。
    """
    global _flush_handle, _flush_task
    if _flush_handle is not None:
        _flush_handle.cancel()
        _flush_handle = None
    if _flush_task is not None and not _flush_task.done():
        _flush_task.cancel()
    _flush_task = None


def schedule_dashboard_summary_push() -> None:
    """Mark summary dirty and ensure a coalesced flush is armed.

    **真实变更入口**（心跳等写入方调用）：会把失败退避计数清零——新数据到了就
    该按正常间隔尽快推一次。失败重试走 :func:`_schedule_retry`（带退避）。
    """
    global _failure_streak
    _failure_streak = 0
    _arm_flush(delay=_push_interval_seconds())


def _schedule_retry() -> None:
    """失败后的重试武装（#2447）：指数退避，超限停到下一次真实变更。"""
    global _failure_streak
    _failure_streak += 1
    if _failure_streak > _MAX_RETRY_STREAK:
        logger.error(
            "dashboard_summary_push_retry_exhausted streak=%d — 停到下一次真实变更",
            _failure_streak,
        )
        return
    delay = min(
        _push_interval_seconds() * (2 ** (_failure_streak - 1)),
        _MAX_RETRY_DELAY_SECONDS,
    )
    logger.warning(
        "dashboard_summary_push_retry streak=%d delay=%.1fs", _failure_streak, delay,
    )
    _arm_flush(delay=delay)


def _arm_flush(delay: float) -> None:
    """置脏并在 ``delay`` 秒后起一次 flush（已有武装/在飞则合并，不叠加）。"""
    global _dirty, _flush_handle
    _dirty = True
    loop = _loop
    if loop is None or loop.is_closed():
        return
    if _flush_handle is not None:
        return

    def _arm_flush_cb() -> None:
        global _flush_handle, _flush_task
        _flush_handle = None
        if loop.is_closed():
            return
        # #2447：串行化——上一次 flush 未完成时不叠加新任务（``_flush_task`` 此前
        # 只写不读）。留脏标记，等它自己收尾后由失败/变更路径再武装。
        if _flush_task is not None and not _flush_task.done():
            logger.debug("dashboard_summary_flush_still_running — 跳过本次武装")
            return
        task = loop.create_task(_flush_dashboard_summary())
        _flush_task = task
        # 运行期必须**保留**引用（此前在 flush 开头清空 → 串行化判据恒为假，
        # 只有任务真正收尾才允许下一次武装）。
        task.add_done_callback(_clear_flush_task)

    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    if running is loop:
        _flush_handle = loop.call_later(delay, _arm_flush_cb)
    else:
        def _schedule_on_loop() -> None:
            global _flush_handle
            if _flush_handle is not None:
                return
            _flush_handle = loop.call_later(delay, _arm_flush_cb)

        loop.call_soon_threadsafe(_schedule_on_loop)


async def _flush_dashboard_summary() -> None:
    global _dirty, _failure_streak
    if not _dirty:
        return
    _dirty = False

    db = SessionLocal()
    try:
        summary = await asyncio.to_thread(compute_dashboard_summary, db)
    except Exception:
        logger.exception("dashboard_summary_compute_failed")
        _schedule_retry()
        return
    finally:
        db.close()

    try:
        from backend.realtime.socketio_server import broadcast_dashboard_summary

        await broadcast_dashboard_summary(summary)
        dashboard_summary_push_total.inc()
        # 成功即清零退避：下次失败从最小延迟重来
        _failure_streak = 0
    except Exception:
        logger.exception("dashboard_summary_broadcast_failed")
        _schedule_retry()
