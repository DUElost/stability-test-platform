"""#1959 — 租约回收器 × 批量续租的 Job/Lease 锁序回归。

`#992` 只统一了 complete × extend-batch 那一侧：`_cas_renew_leases` 的调用契约要求
先锁 `JobInstance`（按 id 升序）再碰 `DeviceLease`。`_reconcile_expired_leases`
是这条约定的**反向镜像**——先 `SELECT … FOR UPDATE` DeviceLease，再
`SELECT … FOR UPDATE` JobInstance。于是「批量续租 × 过期回收」在同一
(job, lease) 两行上形成环路等待：

* 续租事务：持 Job 行锁 → 等 Lease 行锁（CAS `UPDATE device_leases`）
* 回收事务：持 Lease 行锁 → 等 Job 行锁

PostgreSQL 检测到环并回滚其中一方（2026-09-13/14 观测 83 次，48 次卡在
`job_instance` 元组，受害者栈为 `extend_leases_batch`）。

交错之所以真实发生：续租的 prelim 分类读的是**快照**，租约在那个快照里还
有效（判为 renewable，于是先锁 Job），等 CAS 落地时租约已过期，回收器刚好
接手同一行。

本文件钉住两件事：

1. **锁序不变量**（确定性）：回收器在为 Job 行锁排队时，不得已持有 Lease 行锁；
2. **生产形态交错**（并发）：回收器与 `extend_leases_batch` 作用于同一 job，
   不得抛 `DeadlockDetectedError`。

需要 PostgreSQL：SQLite 没有行锁冲突检测，既复现不了死锁，也观察不到锁等待。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import sqlalchemy.exc
from sqlalchemy import select, text

# #2116：需要 PostgreSQL 行锁与死锁检测。本仓 harness 总是提供 PG（conftest 的 testcontainers
# / CI 的 PG service），无 PG 时在 conftest 阶段就报错——**刻意不写**「非 PG 就 skip」的分支：
# 它在 conftest 覆盖 DATABASE_URL 之后不可达，只会把环境问题变成静默跳过。

from backend.api.routes import agent_api as agent_api_mod
from backend.api.routes.agent_api import (
    _ExtendBatchIn,
    _ExtendBatchItemIn,
    extend_leases_batch,
)
from backend.core.database import AsyncSessionLocal, SessionLocal, async_engine
from backend.models.device_lease import DeviceLease
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.job import JobArtifact, JobInstance, StepTrace
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.scheduler.device_lease_reconciler import _reconcile_expired_leases

PIPELINE_DEF = {"lifecycle": {"init": [], "teardown": []}}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _seed(lease_seconds: float) -> dict:
    """1 host + 1 device + 1 RUNNING job + 1 ACTIVE lease。

    ``lease_seconds`` > 0 → 未过期（批量续租 prelim 判为 renewable）；
    < 0 → 已过期（进入回收器候选集）。
    """
    suffix = uuid4().hex[:8]
    host_id = f"lck-{suffix}"
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        host = Host(
            id=host_id, hostname=f"h-{suffix}",
            status=HostStatus.ONLINE.value, created_at=now,
        )
        plan = Plan(
            name=f"lck-{suffix}", description="lock order regression",
            failure_threshold=0.0, created_by="pytest",
        )
        db.add_all([host, plan])
        db.flush()

        device = Device(
            serial=f"LCK-{suffix}", host_id=host_id, status="ONLINE",
            tags=[], created_at=now, adb_connected=True, adb_state="device",
        )
        db.add(device)
        db.flush()

        run = PlanRun(
            plan_id=plan.id, status="RUNNING", failure_threshold=0.0,
            plan_snapshot={"name": plan.name, "plan_id": plan.id},
            run_type="MANUAL", triggered_by="pytest", started_at=now,
        )
        db.add(run)
        db.flush()

        job = JobInstance(
            plan_run_id=run.id, plan_id=plan.id, device_id=device.id,
            host_id=host_id, status=JobStatus.RUNNING.value,
            pipeline_def=PIPELINE_DEF, created_at=now, updated_at=now,
            started_at=now,
        )
        db.add(job)
        db.flush()

        expires_at = now + timedelta(seconds=lease_seconds)
        lease = DeviceLease(
            device_id=device.id, job_id=job.id, host_id=host_id,
            lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=f"{device.id}:1", lease_generation=1,
            agent_instance_id=host_id, acquired_at=now, renewed_at=now,
            expires_at=expires_at,
        )
        db.add(lease)
        db.flush()

        seed = {
            "host_id": host_id,
            "device_id": device.id,
            "job_id": job.id,
            "lease_id": lease.id,
            "plan_id": plan.id,
            "plan_run_id": run.id,
            "token": lease.fencing_token,
            "expires_at": expires_at,
        }
        db.commit()
        return seed
    finally:
        db.close()


def _cleanup(seed: dict) -> None:
    db = SessionLocal()
    try:
        # 断言失败路径下可能还有未收敛的持锁事务：给清理设上限，
        # 避免测试自己无限期挂住（掩盖真正的失败原因）。
        db.execute(text("SET statement_timeout = '15s'"))
        for model, column, key in (
            (DeviceLease, DeviceLease.job_id, "job_id"),
            (StepTrace, StepTrace.job_id, "job_id"),
            (JobArtifact, JobArtifact.job_id, "job_id"),
        ):
            db.query(model).filter(column == seed[key]).delete()
        db.query(DeviceLease).filter(DeviceLease.device_id == seed["device_id"]).delete()
        db.query(JobInstance).filter(JobInstance.id == seed["job_id"]).delete()
        db.query(PlanRun).filter(PlanRun.id == seed["plan_run_id"]).delete()
        db.query(PlanStep).filter(PlanStep.plan_id == seed["plan_id"]).delete()
        db.query(Plan).filter(Plan.id == seed["plan_id"]).delete()
        db.query(Device).filter(Device.id == seed["device_id"]).delete()
        db.query(Host).filter(Host.id == seed["host_id"]).delete()
        db.commit()
    finally:
        db.close()


async def _session_pid(db) -> int:
    return (await db.execute(text("SELECT pg_backend_pid()"))).scalar()


async def _deadlock_count() -> int:
    """本库累计被 PostgreSQL 检测到的死锁次数。

    不能只看任务是否抛异常：回收器把 ``DeadlockDetectedError`` 吞在逐候选的
    ``except Exception`` 里（``_reconcile_expired_leases`` 的
    ``reconciler_expired_lease_failed`` / ``reconciler_job_load_failed``；
    检测面缺口见 #1958），异常不会冒到任务结果上。

    必须用**新事务 + 显式清统计快照**读取：PG 15+ 的统计是写时复制的快照，
    在长期打开的事务里读会拿到旧值（实测漏判过一次）。
    """
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT pg_stat_clear_snapshot()"))
        value = (await db.execute(
            text("SELECT deadlocks FROM pg_stat_database "
                 "WHERE datname = current_database()")
        )).scalar()
        await db.rollback()
        return value


async def _wait_until_blocked(observer, pid: int, timeout: float = 15.0) -> bool:
    """等到 pid 这条后端因等待行锁而阻塞（``wait_event_type='Lock'``）。

    比 sleep 猜时间确定：只有它真的在排队才继续。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        row = (await observer.execute(
            text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
            {"pid": pid},
        )).first()
        if row is not None and row[0] == "Lock":
            return True
        await asyncio.sleep(0.05)
    return False


# ══════════════════════════════════════════════════════════════════════════════
# 回归 1: 锁序不变量 —— 回收器先锁 Job，再碰 Lease
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="module")
async def test_reconciler_locks_job_before_lease():
    """回收器在为 Job 行锁排队时，必须**没有**持有 DeviceLease 行锁。

    构造：另一会话先 ``FOR UPDATE`` 占住 Job 行（等价于 complete /
    extend-batch 已经锁了 Job）。旧实现此时是「持 Lease 等 Job」——正是死锁环
    的一半；正确实现是「等 Job，且尚未碰 Lease」。
    """
    seed = _seed(lease_seconds=-60)
    try:
        await async_engine.dispose()
        async with (
            AsyncSessionLocal() as db_blocker,
            AsyncSessionLocal() as db_observer,
            AsyncSessionLocal() as db_rec,
        ):
            # 占住 Job 行锁（模拟另一条已锁 Job 的路径）
            await db_blocker.execute(
                select(JobInstance)
                .where(JobInstance.id == seed["job_id"])
                .with_for_update()
            )

            rec_pid = await _session_pid(db_rec)
            task = asyncio.create_task(_reconcile_expired_leases(db_rec))

            assert await _wait_until_blocked(db_observer, rec_pid), (
                "回收器应先在 Job 行锁上排队（该行被另一会话持有）"
            )

            # 关键断言：此刻 Lease 行必须仍可被他人立即锁定。
            # 结果先存起来、不让断言在中途抛——否则回收任务会带着行锁被留在
            # 半开状态，清理阶段与它互等而挂住（掩盖真正的失败原因）。
            lease_lock_error: Exception | None = None
            try:
                await db_observer.execute(
                    select(DeviceLease)
                    .where(DeviceLease.id == seed["lease_id"])
                    .with_for_update(nowait=True)
                )
            except sqlalchemy.exc.DBAPIError as exc:  # NOWAIT 命中 55P03
                lease_lock_error = exc
            await db_observer.rollback()

            await db_blocker.rollback()
            unknown, failed, terminal = await asyncio.wait_for(task, timeout=15)
            await db_rec.commit()

            assert lease_lock_error is None, (
                "回收器在等待 Job 行锁时已持有 DeviceLease 行锁 —— "
                f"锁序仍是 Lease→Job（#1959 回归）：{lease_lock_error}"
            )
            assert unknown == 1, f"放开 Job 锁后应正常进入 UNKNOWN；got {unknown}"
            assert failed == 0
            assert terminal == 0

            job = await db_rec.get(JobInstance, seed["job_id"])
            assert job is not None
            assert job.status == JobStatus.UNKNOWN.value
    finally:
        _cleanup(seed)


# ══════════════════════════════════════════════════════════════════════════════
# 回归 2: 生产形态交错 —— 回收器 × extend_leases_batch 同一 job 不死锁
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="module")
async def test_reconciler_and_extend_batch_same_job_no_deadlock(caplog):
    """#1959 修复前，本交错会让 PostgreSQL 检测到死锁。

    交错被显式编排，不靠运气：
      1. 续租先完成 prelim 分类（此时租约仍有效 → renewable）并锁住 Job 行；
      2. 停在 CAS 之前，等租约过期；
      3. 回收器接手同一行（过期候选），在 Job 锁上排队；
      4. 放行 CAS —— 修复前它会在 Lease 行上与回收器互等成环。

    **判据不能只看异常**：修复前死锁的受害者是回收器，而它把
    ``DeadlockDetectedError`` 吞在逐候选的 ``except Exception`` 里，还记成
    ``reconciler_job_load_failed``（误导性日志，检测面缺口见 #1958），
    异常根本不会冒到任务结果上。故本用例断言两个外部信号：
    ``pg_stat_database.deadlocks`` 不增，且回收器没有走失败分支。
    """
    caplog.set_level(
        logging.WARNING, logger="backend.scheduler.device_lease_reconciler",
    )
    seed = _seed(lease_seconds=2.5)
    try:
        await async_engine.dispose()
        go = asyncio.Event()
        cas_entered = asyncio.Event()
        orig_cas = agent_api_mod._cas_renew_leases

        async def gated_cas(db, **kwargs):
            cas_entered.set()
            await go.wait()
            return await orig_cas(db, **kwargs)

        async def _extend():
            async with AsyncSessionLocal() as db:
                return await extend_leases_batch(
                    payload=_ExtendBatchIn(
                        host_id=seed["host_id"],
                        agent_instance_id=seed["host_id"],
                        leases=[_ExtendBatchItemIn(
                            job_id=seed["job_id"],
                            fencing_token=seed["token"],
                            execution_state="EXECUTING_STEP",
                        )],
                    ),
                    db=db, _=None,
                )

        from unittest.mock import patch

        with patch.object(agent_api_mod, "_cas_renew_leases", gated_cas):
            extend_task = asyncio.create_task(_extend())
            # 续租已锁 Job 行（prelim 通过 → 进入 renewable → 锁 Job → 到 CAS 门口）
            await asyncio.wait_for(cas_entered.wait(), timeout=15)

            # 等租约过期，让回收器把它当候选
            now = datetime.now(timezone.utc)
            await asyncio.sleep(max(0.0, (seed["expires_at"] - now).total_seconds()) + 0.5)

            async with (
                AsyncSessionLocal() as db_rec,
                AsyncSessionLocal() as db_observer,
            ):
                rec_pid = await _session_pid(db_rec)
                rec_task = asyncio.create_task(_reconcile_expired_leases(db_rec))
                assert await _wait_until_blocked(db_observer, rec_pid), (
                    "回收器应在 Job 行锁上排队（续租事务持有该行直到提交）"
                )

                deadlocks_before = await _deadlock_count()
                go.set()
                results = await asyncio.wait_for(
                    asyncio.gather(extend_task, rec_task, return_exceptions=True),
                    timeout=45,
                )
                deadlocks_after = await _deadlock_count()
                await db_rec.commit()

                # 终态核对必须在 db_rec 关闭**之前**做完：会话 close 后再拿它
                # 发查询会重新签出一个连接且不再归还，把跨用例的清理（TRUNCATE）
                # 卡死在他人事务的锁上。
                job = await db_rec.get(JobInstance, seed["job_id"])
                assert job is not None
                job_status = job.status
                lease = await db_rec.get(DeviceLease, seed["lease_id"])
                assert lease is not None
                lease_status = lease.status

            errors = [r for r in results if isinstance(r, BaseException)]
            assert not errors, (
                "回收器 × extend-batch 同一 job 不应死锁；got: "
                f"{[type(e).__name__ + ': ' + str(e) for e in errors]}"
            )
            # 判据一：PG 的客观计数不增（修复前受害者可能是吞掉异常的回收器）
            assert deadlocks_after == deadlocks_before, (
                "回收器 × extend-batch 同一 job 触发了 PostgreSQL 死锁检测"
                f"（{deadlocks_after - deadlocks_before} 次）—— #1959 回归"
            )
            # 判据二：回收器没有走失败分支（修复前会记成
            # reconciler_job_load_failed，与真实原因完全不符）
            reconciler_failures = [
                rec.getMessage() for rec in caplog.records
                if rec.name == "backend.scheduler.device_lease_reconciler"
                and rec.levelno >= logging.WARNING
            ]
            assert not reconciler_failures, (
                f"回收器走了失败分支（死锁被吞进通用 except）：{reconciler_failures}"
            )

            # 续租侧：CAS 的 `expires_at > now` 用的是**请求起点**的 now 快照
            # （#992 起的既有语义，本测试不改），故排队等锁的这段时间不影响判定，
            # 这次仍判 renewed —— 关键是它拿到了行锁、没有死锁。
            extend_out = results[0]
            statuses = [item.status for item in extend_out.data.results]
            assert statuses == ["renewed"], f"续租应完成 CAS；got {statuses}"

            # 回收器拿到 Job 锁后重新校验租约：TTL 已被续上 → 本轮跳过，
            # 不把仍在跑的作业误判为过期。
            unknown, _failed, _terminal = results[1]
            assert unknown == 0, f"租约已被续期，回收器应跳过；got {unknown}"

            assert job_status == JobStatus.RUNNING.value, (
                f"作业应保持 RUNNING；got {job_status}"
            )
            assert lease_status == LeaseStatus.ACTIVE.value
    finally:
        _cleanup(seed)
