"""R01-F03（#883）：lifespan 启动失败清理与关闭异常路径。

以 monkeypatch 替身驱动真实 ``lifespan``（TESTING=0 路径）：
- 任一启动阶段失败 → 已启动资源被清理、异常传播、后续阶段不再启动；
- 关闭路径单步失败 → 不阻断后续清理（每步独立容错）。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import backend.main as main_mod


def _fake_redis():
    return SimpleNamespace(ping=AsyncMock(return_value=True), aclose=AsyncMock())


def _fake_scheduler(enter_fails=False, exit_fails=False):
    sched = MagicMock()
    sched.__aenter__ = AsyncMock(side_effect=RuntimeError("enter boom") if enter_fails else None)
    sched.__aexit__ = AsyncMock(side_effect=RuntimeError("exit boom") if exit_fails else None)
    sched.start_in_background = AsyncMock()
    # register_schedules 会 await add_schedule（多张调度表）
    sched.add_schedule = AsyncMock()
    return sched


@pytest.fixture
def lifespan_env(monkeypatch):
    """TESTING=0 + 全部外部依赖替身——真实 lifespan 代码路径。"""
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.setenv("AGENT_SECRET", "lifespan-test-secret")
    monkeypatch.delenv("ENV", raising=False)

    fake_redis = _fake_redis()
    monkeypatch.setattr(main_mod.aioredis, "from_url", AsyncMock(return_value=fake_redis))
    monkeypatch.setattr(
        main_mod, "verify_redis_connectivity", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(main_mod, "_log_redis_ping_ok", lambda *_: None)
    start_saq = AsyncMock()
    monkeypatch.setattr(main_mod, "start_saq_worker", start_saq)
    monkeypatch.setattr(main_mod, "stop_saq_worker", AsyncMock())
    monkeypatch.setattr(
        "backend.core.admission_queue.mark_queue_pump_ready", MagicMock()
    )
    monkeypatch.setattr(
        "backend.core.admission_queue.is_queue_pump_ready", lambda: False
    )
    monkeypatch.setattr(
        "backend.realtime.agent_sid_registry.configure_agent_sid_registry",
        MagicMock(),
    )
    monkeypatch.setattr(main_mod, "capture_main_loop", MagicMock())
    monkeypatch.setattr(main_mod, "init_build_info", MagicMock())
    engine_dispose = AsyncMock()
    # AsyncEngine.dispose read-only（slots）——整体替换模块引用
    monkeypatch.setattr(
        main_mod, "async_engine", SimpleNamespace(dispose=engine_dispose)
    )
    RunConsoleMock = MagicMock()
    monkeypatch.setattr(
        "backend.services.run_console.RunConsole.instance", lambda: RunConsoleMock
    )
    return SimpleNamespace(
        redis=fake_redis,
        engine_dispose=engine_dispose,
        start_saq=start_saq,
    )


@pytest.mark.asyncio
async def test_startup_redis_unreachable_skips_scheduler_and_cleans_up(
    lifespan_env, monkeypatch
):
    """依赖校验先行（#883）：Redis 校验失败 → Scheduler 从未启动，已建资源清理。"""
    monkeypatch.setattr(
        main_mod, "verify_redis_connectivity",
        AsyncMock(side_effect=RuntimeError("Redis unreachable")),
    )
    scheduler_factory = MagicMock()
    monkeypatch.setattr(main_mod, "create_scheduler", scheduler_factory)

    with pytest.raises(RuntimeError, match="Redis unreachable"):
        async with main_mod.lifespan(main_mod._fastapi_app):
            pass

    scheduler_factory.assert_not_called()  # 校验先于副作用启动
    lifespan_env.start_saq.assert_not_awaited()
    main_mod.stop_saq_worker.assert_awaited_once()  # 清理段容错执行
    lifespan_env.redis.aclose.assert_awaited_once()
    lifespan_env.engine_dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_startup_scheduler_enter_failure_cleans_up(lifespan_env, monkeypatch):
    """Scheduler __aenter__ 失败 → SAQ 停止 + Redis/引擎清理，异常传播。"""
    scheduler = _fake_scheduler(enter_fails=True)
    monkeypatch.setattr(main_mod, "create_scheduler", MagicMock(return_value=scheduler))

    with pytest.raises(RuntimeError, match="enter boom"):
        async with main_mod.lifespan(main_mod._fastapi_app):
            pass

    lifespan_env.start_saq.assert_awaited_once()
    main_mod.stop_saq_worker.assert_awaited_once()
    lifespan_env.redis.aclose.assert_awaited_once()
    lifespan_env.engine_dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_saq_stop_failure_does_not_block_rest(lifespan_env, monkeypatch):
    """关闭路径单步失败（#883）：SAQ stop 抛错仍继续 Scheduler/Redis/引擎清理。"""
    scheduler = _fake_scheduler()
    monkeypatch.setattr(main_mod, "create_scheduler", MagicMock(return_value=scheduler))
    monkeypatch.setattr(
        main_mod, "stop_saq_worker", AsyncMock(side_effect=RuntimeError("stop boom"))
    )

    async with main_mod.lifespan(main_mod._fastapi_app):
        pass

    lifespan_env.start_saq.assert_awaited_once()
    scheduler.__aexit__.assert_awaited_once()
    lifespan_env.redis.aclose.assert_awaited_once()
    lifespan_env.engine_dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_scheduler_exit_failure_does_not_block_rest(
    lifespan_env, monkeypatch
):
    """Scheduler __aexit__ 抛错 → Redis/引擎清理继续。"""
    scheduler = _fake_scheduler(exit_fails=True)
    monkeypatch.setattr(main_mod, "create_scheduler", MagicMock(return_value=scheduler))

    async with main_mod.lifespan(main_mod._fastapi_app):
        pass

    main_mod.stop_saq_worker.assert_awaited_once()
    scheduler.__aexit__.assert_awaited_once()
    lifespan_env.redis.aclose.assert_awaited_once()
    lifespan_env.engine_dispose.assert_awaited_once()
