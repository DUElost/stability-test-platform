"""Coalesced dashboard summary publisher (#2324)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.services import dashboard_summary_publisher as pub


@pytest.mark.asyncio
async def test_schedule_coalesces_to_single_broadcast(monkeypatch):
    monkeypatch.setenv("STP_DASHBOARD_SUMMARY_PUSH_INTERVAL_SECONDS", "0.05")
    pub._reset_for_tests()

    loop = asyncio.get_running_loop()
    pub.bind_event_loop(loop)

    summary = {
        "hosts": {"total": 1},
        "devices": {"total": 0},
        "alerts": {"total": 0},
        "host_resources": [],
    }
    broadcast = AsyncMock()

    with patch(
        "backend.services.dashboard_summary_publisher.compute_dashboard_summary",
        return_value=summary,
    ), patch(
        "backend.services.dashboard_summary_publisher.SessionLocal",
    ) as session_local, patch(
        "backend.realtime.socketio_server.broadcast_dashboard_summary",
        broadcast,
    ), patch(
        "backend.services.dashboard_summary_publisher.dashboard_summary_push_total",
    ) as counter:
        session_local.return_value.close = lambda: None

        for _ in range(5):
            pub.schedule_dashboard_summary_push()

        await asyncio.sleep(0.2)

        assert broadcast.await_count == 1
        broadcast.assert_awaited_once_with(summary)
        assert counter.inc.call_count == 1

    pub._reset_for_tests()


@pytest.mark.asyncio
async def test_failures_back_off_and_eventually_stop(monkeypatch):
    """#2447：失败不再按正常间隔无限重试——指数退避，超限停到下一次真实变更。"""
    monkeypatch.setenv("STP_DASHBOARD_SUMMARY_PUSH_INTERVAL_SECONDS", "0.01")
    pub._reset_for_tests()
    pub.bind_event_loop(asyncio.get_running_loop())

    delays: list[float] = []

    def _fake_arm(delay: float) -> None:
        delays.append(round(delay, 4))

    with patch.object(pub, "_arm_flush", _fake_arm):
        for _ in range(pub._MAX_RETRY_STREAK + 1):
            pub._schedule_retry()

    # 退避序列 1×,2×,4×,8×…（以正常间隔为基数），且**不随失败无限增长**
    assert delays == [0.01, 0.02, 0.04, 0.08, 0.16]
    assert pub._failure_streak == pub._MAX_RETRY_STREAK + 1  # 超限后只记不给延迟

    # 真实变更入口清零 streak：新数据到了就该按正常间隔重试
    with patch.object(pub, "_arm_flush", _fake_arm):
        pub.schedule_dashboard_summary_push()
    assert pub._failure_streak == 0
    assert delays[-1] == 0.01

    pub._reset_for_tests()


@pytest.mark.asyncio
async def test_retry_delay_is_capped(monkeypatch):
    """上界：退避不得超过 `_MAX_RETRY_DELAY_SECONDS`（长间隔配置下也一样）。"""
    monkeypatch.setenv("STP_DASHBOARD_SUMMARY_PUSH_INTERVAL_SECONDS", "10")
    pub._reset_for_tests()
    pub.bind_event_loop(asyncio.get_running_loop())

    delays: list[float] = []
    with patch.object(pub, "_arm_flush", lambda delay: delays.append(delay)):
        for _ in range(3):
            pub._schedule_retry()

    assert delays == [10, 20, pub._MAX_RETRY_DELAY_SECONDS]

    pub._reset_for_tests()


@pytest.mark.asyncio
async def test_shutdown_cancels_pending_flush(monkeypatch):
    """#2447：关闭序列要能撤销已武装的 flush（此前模块没有对外 teardown）。"""
    monkeypatch.setenv("STP_DASHBOARD_SUMMARY_PUSH_INTERVAL_SECONDS", "5")
    pub._reset_for_tests()
    pub.bind_event_loop(asyncio.get_running_loop())

    with patch(
        "backend.services.dashboard_summary_publisher.compute_dashboard_summary",
        return_value={},
    ):
        pub.schedule_dashboard_summary_push()
        assert pub._flush_handle is not None

        pub.shutdown_dashboard_summary_publisher()
        assert pub._flush_handle is None
        assert pub._flush_task is None

    pub._reset_for_tests()


@pytest.mark.asyncio
async def test_flush_is_serialized_no_overlapping_task(monkeypatch):
    """#2447：上一次 flush 未完成时不得叠加新任务（`_flush_task` 此前只写不读）。"""
    import threading

    monkeypatch.setenv("STP_DASHBOARD_SUMMARY_PUSH_INTERVAL_SECONDS", "0.01")
    pub._reset_for_tests()
    pub.bind_event_loop(asyncio.get_running_loop())

    release = threading.Event()
    started = threading.Event()
    calls: list[int] = []

    def _slow_compute(_db):
        calls.append(1)
        started.set()
        release.wait(timeout=2.0)
        return {}

    broadcast = AsyncMock()
    with patch(
        "backend.services.dashboard_summary_publisher.compute_dashboard_summary",
        _slow_compute,
    ), patch(
        "backend.services.dashboard_summary_publisher.SessionLocal",
    ) as session_local, patch(
        "backend.realtime.socketio_server.broadcast_dashboard_summary",
        broadcast,
    ), patch(
        "backend.services.dashboard_summary_publisher.dashboard_summary_push_total",
    ):
        session_local.return_value.close = lambda: None

        pub.schedule_dashboard_summary_push()
        assert await asyncio.to_thread(started.wait, 1.0)

        # 第一个 flush 卡在 compute 里时，再触发一次（真实变更）→ 武装到点后必须跳过
        pub.schedule_dashboard_summary_push()
        await asyncio.sleep(0.1)

        assert len(calls) == 1, "上一次 flush 未完成时不应叠加新任务"

        release.set()
        # #2799：在飞期间到达的变更必须**最终落地**——收尾回调补武装后第二次推送发出。
        # 本用例此前断言 `await_count == 1`，实际把「丢唤醒」固化成了期望行为。
        for _ in range(40):
            if broadcast.await_count >= 2:
                break
            await asyncio.sleep(0.05)
        assert broadcast.await_count == 2, (
            "在飞期间到达的变更没有被补推 —— 丢唤醒（#2799）：收尾回调未重武装"
        )

    pub._reset_for_tests()


@pytest.mark.asyncio
async def test_compute_hang_times_out_and_retries(monkeypatch):
    """#2799：compute 挂起必须有超时出口——否则串行化判据让推送永久冻结。

    反向自证：无超时实现下 `_flush_task` 永不收尾，后续每次武装都被跳过，本用例的
    `_failure_streak >= 1`（超时走失败重试）不会成立。
    """
    import threading

    monkeypatch.setenv("STP_DASHBOARD_SUMMARY_PUSH_INTERVAL_SECONDS", "0.01")
    pub._reset_for_tests()
    monkeypatch.setattr(pub, "_COMPUTE_TIMEOUT_SECONDS", 0.05)
    pub.bind_event_loop(asyncio.get_running_loop())

    release = threading.Event()

    def _hang(_db):
        release.wait(timeout=1.0)   # 线程无法取消：给个上界，别拖住测试进程退出
        return {}

    broadcast = AsyncMock()
    try:
        with patch(
            "backend.services.dashboard_summary_publisher.compute_dashboard_summary",
            _hang,
        ), patch(
            "backend.services.dashboard_summary_publisher.SessionLocal",
        ) as session_local, patch(
            "backend.realtime.socketio_server.broadcast_dashboard_summary",
            broadcast,
        ), patch(
            "backend.services.dashboard_summary_publisher.dashboard_summary_push_total",
        ):
            session_local.return_value.close = lambda: None

            pub.schedule_dashboard_summary_push()
            await asyncio.sleep(0.3)

            assert pub._failure_streak >= 1, "超时必须走失败路径（退避重试），不是静默丢弃"
            assert broadcast.await_count == 0, "挂起的 compute 不该产生推送"
            assert pub._flush_task is None, "超时的 flush 必须收尾，否则后续武装全被跳过"
    finally:
        release.set()
        pub._reset_for_tests()


@pytest.mark.asyncio
async def test_hang_timeout_does_not_multiplicate_under_heartbeats(monkeypatch):
    """#2882：超时出口不得演变成线程增殖。

    挂起期间心跳持续 `schedule_dashboard_summary_push()`（每次把 `_failure_streak`
    清零并再武装）——旧实现里每轮武装都起新 compute 线程（flush 已因超时收尾，
    串行化判据形同虚设），占满默认执行器与池连接。修复＝在飞判据（线程真正收尾
    前不再起第二个 compute）＋线程收尾后补推待推的脏数据（不丢唤醒）。

    反向自证：无在飞判据时，本用例挂起阶段的 `len(calls) == 1` 必红（心跳×退避
    会叠出多次 compute）。
    """
    import threading

    monkeypatch.setenv("STP_DASHBOARD_SUMMARY_PUSH_INTERVAL_SECONDS", "0.01")
    pub._reset_for_tests()
    monkeypatch.setattr(pub, "_COMPUTE_TIMEOUT_SECONDS", 0.05)
    pub.bind_event_loop(asyncio.get_running_loop())

    release = threading.Event()
    started = threading.Event()
    calls: list[int] = []

    def _hang(_db):
        calls.append(1)
        started.set()
        release.wait(timeout=1.5)
        return {}

    broadcast = AsyncMock()
    try:
        with patch(
            "backend.services.dashboard_summary_publisher.compute_dashboard_summary",
            _hang,
        ), patch(
            "backend.services.dashboard_summary_publisher.SessionLocal",
        ) as session_local, patch(
            "backend.realtime.socketio_server.broadcast_dashboard_summary",
            broadcast,
        ), patch(
            "backend.services.dashboard_summary_publisher.dashboard_summary_push_total",
        ):
            session_local.return_value.close = lambda: None

            pub.schedule_dashboard_summary_push()
            assert await asyncio.to_thread(started.wait, 1.0)

            # 超时发生（0.05s）后连续 6 次心跳——每次都会清零退避并再武装
            for _ in range(6):
                await asyncio.sleep(0.05)
                pub.schedule_dashboard_summary_push()
            await asyncio.sleep(0.1)

            assert len(calls) == 1, (
                f"挂起期间起了 {len(calls)} 个 compute——超时线程增殖未挡住（#2882）"
            )
            assert broadcast.await_count == 0, "挂起的 compute 不该产生推送"

            # 线程真正收尾：在飞清除，脏标记（心跳期间保持置位）必须被补推
            release.set()
            for _ in range(40):
                if len(calls) >= 2 and broadcast.await_count >= 1:
                    break
                await asyncio.sleep(0.05)
            assert len(calls) == 2, "线程收尾后没有补算——在飞判据丢了唤醒"
            assert broadcast.await_count >= 1, "补算结果没有补推"
    finally:
        release.set()
        pub._reset_for_tests()


@pytest.mark.asyncio
async def test_compute_runs_on_dedicated_executor(monkeypatch):
    """#2882：compute 必须排**专用**执行器，不得再进全进程共享的默认执行器。

    判别力：`run_in_executor(None, ...)`（=默认池）会让 seen 收进 None；本用例
    断言收进的是 publisher 私有 executor 单例。
    """
    monkeypatch.setenv("STP_DASHBOARD_SUMMARY_PUSH_INTERVAL_SECONDS", "0.01")
    pub._reset_for_tests()
    loop = asyncio.get_running_loop()
    pub.bind_event_loop(loop)

    seen: list = []
    real = loop.run_in_executor

    def _spy(executor, fn, *args, **kw):
        seen.append(executor)
        return real(executor, fn, *args, **kw)

    monkeypatch.setattr(loop, "run_in_executor", _spy)

    with patch(
        "backend.services.dashboard_summary_publisher.compute_dashboard_summary",
        return_value={},
    ), patch(
        "backend.services.dashboard_summary_publisher.SessionLocal",
    ) as session_local, patch(
        "backend.realtime.socketio_server.broadcast_dashboard_summary",
        AsyncMock(),
    ), patch(
        "backend.services.dashboard_summary_publisher.dashboard_summary_push_total",
    ):
        session_local.return_value.close = lambda: None
        pub.schedule_dashboard_summary_push()
        for _ in range(40):
            if seen:
                break
            await asyncio.sleep(0.02)

    assert seen, "compute 没有经过 run_in_executor"
    assert seen[0] is pub._get_executor(), "compute 排进了共享/默认执行器（#2882）"
    assert getattr(seen[0], "_max_workers", None) == 1

    pub._reset_for_tests()


@pytest.mark.asyncio
async def test_failure_path_actually_uses_backoff(monkeypatch):
    """接线判据：#2447 的退避必须由**失败路径**触发（不是只在单测里直接调 `_schedule_retry`）。

    判别力：第一次失败的重试延迟与正常间隔相同（1×），第二次起才是 2× ——
    若失败分支仍走 `schedule_dashboard_summary_push()`（恒定间隔），这里不会出现 0.02。
    """
    monkeypatch.setenv("STP_DASHBOARD_SUMMARY_PUSH_INTERVAL_SECONDS", "0.01")
    pub._reset_for_tests()
    pub.bind_event_loop(asyncio.get_running_loop())

    delays: list[float] = []
    real_arm = pub._arm_flush

    def _recording_arm(delay: float) -> None:
        delays.append(round(delay, 4))
        real_arm(delay)

    with patch.object(pub, "_arm_flush", _recording_arm), patch(
        "backend.services.dashboard_summary_publisher.SessionLocal",
    ) as session_local, patch(
        "backend.services.dashboard_summary_publisher.compute_dashboard_summary",
        side_effect=RuntimeError("db down"),
    ):
        session_local.return_value.close = lambda: None

        pub.schedule_dashboard_summary_push()
        await asyncio.sleep(0.15)

    assert 0.02 in delays, f"失败路径没有退避：{delays}"
    assert pub._failure_streak >= 2

    pub._reset_for_tests()
