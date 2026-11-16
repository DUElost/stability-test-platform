"""#4025 回归测试：asyncio.to_thread 会话所有权与 TRUNCATE 锁超时取证。

验证三层根因防线：
1. ``/api/v1/heartbeat`` 在协程被 cancel 后，worker 线程持独立 Session 收尾，
   连接池不遗留 checkedout / idle-in-transaction 连接；同时继续兼容 ``get_db`` 覆写。
2. ``precheck.runner.drive_dispatch_gate`` 自建 Session 时不跨 ``asyncio.to_thread``
   传递外层 Session，取消后工作线程持独立 Session 安全收尾。
3. ``_truncate_all_tables`` 在单向锁等下按 ``SET LOCAL lock_timeout`` 快速失败，
   并通过 ``_dump_deadlock_scene`` 捕获 ``idle in transaction`` 持锁方的 relation 锁。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

import backend.tests.conftest as tests_conftest
from backend.api.routes import heartbeat as heartbeat_route_mod
from backend.core.database import get_db
from backend.main import fastapi_app
from backend.models.plan_run import PlanRun
from backend.services.precheck import runner as precheck_runner
from backend.tests.services.precheck_helpers import ack_ok


async def test_heartbeat_cancellation_does_not_leak_session(engine, monkeypatch):
    """协程在 to_thread 期间被 cancel，连接池 checkedout 归零且无悬空事务。"""
    tracked_engine = create_engine(
        engine.url,
        poolclass=QueuePool,
        pool_size=2,
        max_overflow=0,
        future=True,
    )
    TrackedSession = sessionmaker(
        autocommit=False, autoflush=False, bind=tracked_engine, future=True,
    )
    monkeypatch.setattr(heartbeat_route_mod, "SessionLocal", TrackedSession)
    monkeypatch.setenv("AGENT_SECRET", "4025-secret")

    entered = threading.Event()
    release = threading.Event()
    worker_done = threading.Event()
    observed: dict[str, object] = {}

    def _slow_heartbeat(payload, db):
        observed["db"] = db
        db.execute(text("SELECT 1"))
        entered.set()
        assert release.wait(timeout=5.0), "主协程未及时释放 worker"
        # 模拟主协程取消后，worker 继续在同一 Session 上执行尾部查询（如 open_debts_for_host_sync）
        db.execute(text("SELECT 1"))
        observed["in_tx_before_return"] = db.in_transaction()
        worker_done.set()
        return {"ok": True, "host_id": payload.host_id}, []

    monkeypatch.setattr(
        heartbeat_route_mod, "_process_heartbeat_with_db", _slow_heartbeat,
    )
    fastapi_app.dependency_overrides.pop(get_db, None)

    try:
        transport = httpx.ASGITransport(app=fastapi_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-Agent-Secret": "4025-secret"},
            timeout=10.0,
        ) as client:
            task = asyncio.create_task(
                client.post(
                    "/api/v1/heartbeat",
                    json={"host_id": "hb-cancel-4025", "status": "ONLINE", "devices": []},
                )
            )
            assert await asyncio.to_thread(entered.wait, 5.0), "worker 未进入"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            release.set()
            assert await asyncio.to_thread(worker_done.wait, 5.0), "worker 未收尾"
            # 等 asyncio.to_thread 返回后 Session.__exit__ 完成
            deadline = time.monotonic() + 5.0
            while tracked_engine.pool.checkedout() > 0 and time.monotonic() < deadline:
                await asyncio.sleep(0.01)

        assert observed.get("in_tx_before_return") is True
        assert tracked_engine.pool.checkedout() == 0, (
            f"heartbeat 取消后泄漏连接: checkedout={tracked_engine.pool.checkedout()}"
        )
    finally:
        fastapi_app.middleware_stack = None
        tracked_engine.dispose()


def test_heartbeat_respects_get_db_override(client, db_session, sample_host, monkeypatch):
    """测试夹具覆写 get_db 时，heartbeat 仍走同一测试会话。"""
    monkeypatch.setenv("AGENT_SECRET", "4025-secret")
    resp = client.post(
        "/api/v1/heartbeat",
        headers={"X-Agent-Secret": "4025-secret"},
        json={"host_id": sample_host.id, "status": "ONLINE", "devices": []},
    )
    assert resp.status_code == 200
    assert resp.json()["host_id"] == sample_host.id


async def test_drive_dispatch_gate_cancellation_isolates_worker_session(
    engine, single_device_gate_chain, monkeypatch,
):
    """drive_dispatch_gate(db=None) 在 to_thread 期间取消时，worker 使用独立 Session。"""
    tracked_engine = create_engine(
        engine.url,
        poolclass=QueuePool,
        pool_size=2,
        max_overflow=0,
        future=True,
    )
    TrackedSession = sessionmaker(
        autocommit=False, autoflush=False, bind=tracked_engine, future=True,
    )

    caller_db = TrackedSession()
    pr = PlanRun(
        plan_id=single_device_gate_chain["plan"].id,
        status="RUNNING",
        run_type="MANUAL",
        plan_snapshot={
            "name": "single-device-plan",
            "steps": [{"script_name": "check_device", "script_version": "1.0.0"}],
        },
        run_context={"dispatch_device_ids": [single_device_gate_chain["device"].id]},
    )
    caller_db.add(pr)
    caller_db.commit()
    plan_run_id = pr.id
    caller_db.close()

    created_sessions: list[object] = []

    def _session_factory():
        s = TrackedSession()
        created_sessions.append(s)
        return s

    monkeypatch.setattr(precheck_runner, "SessionLocal", _session_factory)
    monkeypatch.setattr(
        "backend.services.precheck.state.flag_modified", lambda *_a, **_k: None,
    )

    entered = threading.Event()
    release = threading.Event()
    worker_done = threading.Event()
    seen_dbs: list[object] = []

    async def _fake_call(host_id, _event, _data, *, timeout=10.0):
        return ack_ok(host_id, "aabbcc11")

    def _slow_complete(_run_id, db):
        seen_dbs.append(db)
        db.execute(text("SELECT 1"))
        entered.set()
        assert release.wait(timeout=5.0)
        db.execute(text("SELECT 1"))
        worker_done.set()

    monkeypatch.setattr("backend.services.precheck.verify.call_agent_rpc", _fake_call)
    monkeypatch.setattr(precheck_runner, "complete_plan_run_dispatch", _slow_complete)

    try:
        task = asyncio.create_task(precheck_runner.drive_dispatch_gate(plan_run_id))
        assert await asyncio.to_thread(entered.wait, 5.0)
        outer_db = created_sessions[0]
        assert len(seen_dbs) == 1
        assert seen_dbs[0] is not outer_db, "worker 必须自开 Session，不得复用外层 Session"

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        release.set()
        assert await asyncio.to_thread(worker_done.wait, 5.0)
        deadline = time.monotonic() + 5.0
        while tracked_engine.pool.checkedout() > 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)

        assert tracked_engine.pool.checkedout() == 0, (
            f"drive_dispatch_gate 取消后泄漏连接: checkedout={tracked_engine.pool.checkedout()}"
        )
    finally:
        tracked_engine.dispose()


def test_truncate_lock_timeout_and_dumps_relation_blocker(engine, monkeypatch, caplog):
    """#4025：idle-in-transaction 持 AccessShareLock 时，清库按 lock_timeout 快速失败并取证。"""
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL only")

    monkeypatch.setattr(tests_conftest, "_TRUNCATE_LOCK_TIMEOUT", "200ms")

    with engine.connect() as blocker:
        blocker.execute(text('SELECT count(*) FROM "host"'))
        assert blocker.in_transaction()

        with caplog.at_level(logging.WARNING, logger="backend.tests.truncate_deadlock"):
            started = time.monotonic()
            with pytest.raises(OperationalError):
                tests_conftest._truncate_all_tables(engine, '"host"')
            elapsed = time.monotonic() - started
        blocker.rollback()

    assert elapsed < 5.0, f"TRUNCATE 未按 lock_timeout 快速失败: elapsed={elapsed:.2f}s"
    scene_logs = [r.message for r in caplog.records if "TRUNCATE_DEADLOCK_SCENE" in r.message]
    assert scene_logs, f"未输出 TRUNCATE_DEADLOCK_SCENE: {[r.message for r in caplog.records]}"
    assert "host" in scene_logs[0]
    assert "AccessShareLock" in scene_logs[0]
