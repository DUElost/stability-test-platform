# -*- coding: utf-8 -*-
"""Coalesced dashboard-summary WS push (#2324 / ADR-0026 observation plane).

Heartbeat and other writers mark a dirty flag; a single flush ≤1Hz (default)
recomputes the summary and broadcasts ``dashboard_summary``.
"""
from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
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

# #2799：单次 compute 的墙钟上界。compute 是同步 DB 聚合（to_thread）——挂起时既没有
# 超时也没有取消路径，flush 任务永不收尾 ⇒ 串行化判据（`_flush_task` 未完成）让后续
# 每次武装都被跳过，推送**永久冻结且无自愈**。超时走失败路径（退避重试），并让会话
# 由线程自身收尾（见 `_flush_dashboard_summary` 的 `_compute_and_close`）。
# #2882 残余：超时只终结「等待」，终结不了线程——挂起线程继续占**全进程共享的默认
# 执行器**与池连接，而心跳持续把 `_failure_streak` 清零再武装 ⇒ 退避上限失效、
# 线程按重试节奏增殖。本模块因此改用①专用单线程执行器（隔离共享池）＋②在飞判据
# （`_compute_future` 绑定线程真正收尾，见 `_on_compute_done`）。
_COMPUTE_TIMEOUT_SECONDS = 30.0

_loop: Optional[asyncio.AbstractEventLoop] = None
_dirty: bool = False
_flush_handle: Optional[asyncio.TimerHandle] = None
_flush_task: Optional[asyncio.Task] = None
_failure_streak: int = 0
# #2882：compute **线程**的 future（超时后仍在飞的那条线程）。与 `_flush_task`
# 区分：flush 可以已收尾而线程仍在跑。
_compute_future: Optional["asyncio.Future[dict]"] = None
# #2882：publisher 专用单线程执行器（懒建，_reset/shutdown 时销毁重建）。
_executor: Optional[ThreadPoolExecutor] = None


def _get_executor() -> ThreadPoolExecutor:
    """#2882：专用单线程执行器——挂起 compute 不得再挤占默认执行器
    （`saq_tasks` / `heartbeat.py` / `logs.py` 的 `to_thread` 都排在同一条默认池上）。"""
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="dashboard-summary",
        )
    return _executor


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
    global _compute_future, _executor
    if _flush_handle is not None:
        _flush_handle.cancel()
    if _flush_task is not None and not _flush_task.done():
        _flush_task.cancel()
    _loop = None
    _dirty = False
    _flush_handle = None
    _flush_task = None
    _failure_streak = 0
    _compute_future = None
    if _executor is not None:
        # #2882：测试间销毁专用执行器（在飞线程靠测试用例自身的 release 收尾；
        # 未跑完的排队项直接取消）。
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None


def _clear_flush_task(task: asyncio.Task) -> None:
    """flush 任务收尾后清引用（只在仍是当前任务时清，避免清掉后来者）。"""
    global _flush_task
    if _flush_task is task:
        _flush_task = None
    # #2799：**丢唤醒收口**。在飞期间到达的变更置了 `_dirty` 并武装 timer，而 timer 到点
    # 撞上「任务未完成」会被跳过且不重武装（#2447 的注释自陈「等它自己收尾后由失败/变更
    # 路径再武装」——但成功路径不武装）。收尾处统一补一次：`_arm_flush` 自身幂等
    # （已有 handle/在飞则不叠加），故这里是安全网而不是第二条触发路径。
    # #2882：compute 线程仍在飞时这里补的武装会在 `_arm_flush_cb` 被跳过，
    # `_dirty` 保持置位、由 `_on_compute_done` 在线程真正收尾时再补——不丢唤醒。
    if _dirty and _flush_handle is None:
        _arm_flush(delay=0.0)


def _on_compute_done(fut: "asyncio.Future[dict]") -> None:
    """#2882：compute **线程真正收尾**（而非 flush 收尾）的回调。

    超时路径里 flush 早已返回，本回调是「挂起线程结束」的唯一信号：清在飞引用，
    并在脏标记仍在（跳过的武装不擦脏）时补一次推送——增殖被挡住的同时不丢唤醒。
    先消费 result/exception，避免线程迟到抛错时的「never retrieved」噪声。
    """
    global _compute_future
    if not fut.cancelled():
        fut.exception()
    if _compute_future is fut:
        _compute_future = None
    if _dirty and _flush_handle is None:
        _arm_flush(delay=0.0)


def shutdown_dashboard_summary_publisher() -> None:
    """取消待执行的 flush（进程关闭序列用，#2447）。

    此前模块自带的 ``_reset_for_tests()`` 从未接入 lifespan 清理：关闭窗口里若恰好
    有一次 flush 已武装，它会在引擎/DB 正在关闭时触发，日志噪声之外没有任何收益。
    """
    global _flush_handle, _flush_task, _dirty, _compute_future, _executor
    if _flush_handle is not None:
        _flush_handle.cancel()
        _flush_handle = None
    if _flush_task is not None and not _flush_task.done():
        _flush_task.cancel()
    _flush_task = None
    # #2799：关闭即终止推送——清脏标记，避免被取消的任务在收尾回调里又补一次武装。
    _dirty = False
    # #2882：在飞 future 与专用执行器一并收口（wait=False：挂起线程不能拖住退出序列）。
    _compute_future = None
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None


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
        # #2882：flush 收尾 ≠ compute 线程收尾——超时后的线程仍在飞，再起一次
        # compute 就是增殖（每条挂线程占一条池连接）。脏标记保持，线程收尾由
        # `_on_compute_done` 补武装。
        if _compute_future is not None and not _compute_future.done():
            logger.warning(
                "dashboard_summary_compute_inflight — 超时的 compute 线程仍在跑，"
                "跳过本次武装（其收尾后自动补推）",
            )
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
    global _dirty, _failure_streak, _compute_future
    if not _dirty:
        return
    _dirty = False

    def _compute_and_close() -> dict:
        # #2799：会话由**线程自己**收尾——超时后 `wait_for` 会先返回而线程仍在跑，
        # 若由外层 finally 关会话，正在执行的查询会撞上已关闭的连接。
        db = SessionLocal()
        try:
            return compute_dashboard_summary(db)
        finally:
            db.close()

    loop = asyncio.get_running_loop()  # flush 只可能作为 loop task 运行（_arm_flush_cb 里 create_task）
    fut = loop.run_in_executor(_get_executor(), _compute_and_close)
    _compute_future = fut
    # #2882：shield 让超时只终结「等待」不触碰 future——`_on_compute_done`
    # 因此绑定在**线程真正收尾**上（在飞清除 + 补推）。
    fut.add_done_callback(_on_compute_done)

    try:
        summary = await asyncio.wait_for(
            asyncio.shield(fut),
            timeout=_COMPUTE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        # #2799：compute 挂起必须有出口——否则 flush 任务永不收尾，串行化判据把后续
        # 每次武装都跳过，推送永久冻结（线程无法取消，交给它自己收尾）。
        # #2882：此处 flush 收尾而线程在飞——后续武装会被 `_arm_flush_cb` 的
        # 在飞判据挡住，不再有第二次 compute 起线程。
        logger.error(
            "dashboard_summary_compute_timeout timeout=%.1fs — 走失败重试",
            _COMPUTE_TIMEOUT_SECONDS,
        )
        _schedule_retry()
        return
    except Exception:
        logger.exception("dashboard_summary_compute_failed")
        _schedule_retry()
        return

    try:
        from backend.realtime.socketio_server import broadcast_dashboard_summary

        await broadcast_dashboard_summary(summary)
        dashboard_summary_push_total.inc()
        # 成功即清零退避：下次失败从最小延迟重来
        _failure_streak = 0
    except Exception:
        logger.exception("dashboard_summary_broadcast_failed")
        _schedule_retry()
