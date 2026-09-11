"""#994：Cron 严格防重叠策略回归。

裁决：任一非终态 Run（QUEUED / PRECHECK / RUNNING）阻断新窗口；长跑不豁免、
错过不补跑，仅推进 ``next_run_at``。旧实现只查「10 分钟内的 RUNNING」——
本文件同时锁住「排队态阻断」「长跑仍阻断」「终态放行」与「抖动去重先于重叠」
四个方向。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from backend.core.database import AsyncSessionLocal, async_engine
from backend.models.enums import PlanRunStatus
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.schedule import TaskSchedule, schedule_timestamp
from backend.scheduler.cron_scheduler import _fire_schedule

CRON = "*/5 * * * *"

TERMINAL = (
    PlanRunStatus.SUCCESS.value,
    PlanRunStatus.PARTIAL_SUCCESS.value,
    PlanRunStatus.FAILED.value,
)


@pytest.fixture
def plan_row(db_session):
    plan = Plan(
        name="cron-overlap-plan", description="cron overlap policy test",
        failure_threshold=0.1, created_by="test",
    )
    db_session.add(plan)
    db_session.flush()
    return plan


def _seed_schedule(db_session, plan) -> TaskSchedule:
    sched = TaskSchedule(
        name="cron-overlap-sched", cron_expression=CRON, plan_id=plan.id,
        enabled=True, device_ids=[],
        next_run_at=schedule_timestamp(datetime.now(timezone.utc)),
    )
    db_session.add(sched)
    db_session.flush()
    return sched


def _seed_run(
    db_session, plan, status, *,
    started_delta: timedelta = timedelta(seconds=5),
    run_context: dict | None = None,
) -> PlanRun:
    run = PlanRun(
        plan_id=plan.id,
        status=status,
        plan_snapshot={},
        run_type="SCHEDULE",
        run_context=run_context if run_context is not None else {},
        started_at=datetime.now(timezone.utc) - started_delta,
    )
    db_session.add(run)
    db_session.flush()
    return run


async def _fire(db_session, sched):
    """与生产同构的 async session 触发一次；返回 (dispatch mock, (next, last))。"""
    await async_engine.dispose()
    with patch(
        "backend.services.plan_dispatcher.dispatch_plan", new_callable=AsyncMock,
    ) as dispatch:
        async with AsyncSessionLocal() as adb:
            sched_async = await adb.get(TaskSchedule, sched.id)
            await _fire_schedule(adb, sched_async, datetime.now(timezone.utc))
            window = (sched_async.next_run_at, sched_async.last_run_at)
        return dispatch, window


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [PlanRunStatus.QUEUED.value, PlanRunStatus.PRECHECK.value],
)
async def test_queued_states_block_window(db_session, plan_row, status):
    """排队态（QUEUED/PRECHECK）也阻断——旧实现只查 RUNNING 会积压。"""
    sched = _seed_schedule(db_session, plan_row)
    _seed_run(db_session, plan_row, status)
    db_session.commit()

    dispatch, (next_run, last_run) = await _fire(db_session, sched)

    assert dispatch.await_count == 0
    assert last_run is None          # 跳过不写 last_run_at
    assert next_run is not None      # 仅推进 next_run_at


@pytest.mark.asyncio
async def test_long_running_run_still_blocks_window(db_session, plan_row):
    """长跑超过旧的 10 分钟阈值仍阻断（#994 去掉年龄豁免）。"""
    sched = _seed_schedule(db_session, plan_row)
    _seed_run(
        db_session, plan_row, PlanRunStatus.RUNNING.value,
        started_delta=timedelta(hours=2),
    )
    db_session.commit()

    dispatch, _ = await _fire(db_session, sched)

    assert dispatch.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("status", TERMINAL)
async def test_terminal_runs_do_not_block(db_session, plan_row, status):
    sched = _seed_schedule(db_session, plan_row)
    _seed_run(db_session, plan_row, status)
    db_session.commit()

    dispatch, (_next, last_run) = await _fire(db_session, sched)

    assert dispatch.await_count == 1
    assert last_run is not None


@pytest.mark.asyncio
async def test_schedule_dedup_precedes_overlap(db_session, plan_row):
    """60s 内同 schedule 已产出 root Run → 抖动去重先跳过（即使无未终态 Run）。"""
    sched = _seed_schedule(db_session, plan_row)
    _seed_run(
        db_session, plan_row, PlanRunStatus.SUCCESS.value,
        started_delta=timedelta(seconds=5),
        run_context={"schedule_id": sched.id},
    )
    db_session.commit()

    dispatch, _ = await _fire(db_session, sched)

    assert dispatch.await_count == 0
