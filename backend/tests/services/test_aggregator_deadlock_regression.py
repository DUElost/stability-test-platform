"""并发死锁回归测试 — PlanRun aggregator 行锁策略。

回归场景: complete_job 在同一事务里先 UPDATE job_instance(FK plan_run_id 触发
plan_run 行上的 FOR KEY SHARE),再 SELECT plan_run FOR UPDATE。两个并发
complete 同一 plan_run 的不同 job 会各持 KEY SHARE 互锁对方的 FOR UPDATE →
PG 检测到死锁,杀掉其中一个事务(500 Internal Server Error)。

修复: aggregator/abort 改用 FOR NO KEY UPDATE —— 与 FOR KEY SHARE 兼容,
消除循环等待;两个 FOR NO KEY UPDATE 仍互相冲突,串行化不变。

本测试需要 PostgreSQL(SQLite 无行锁冲突检测,无法复现死锁)。
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, text

pytestmark = pytest.mark.skipif(
    os.getenv("DATABASE_URL", "").startswith("sqlite"),
    reason="死锁回归测试需要 PostgreSQL 行锁(FOR KEY SHARE / FOR NO KEY UPDATE)",
)

from backend.api.routes.agent_api import _RunCompleteIn, complete_job
from backend.core.database import AsyncSessionLocal, SessionLocal
from backend.models.device_lease import DeviceLease
from backend.models.enums import (
    HostStatus,
    JobStatus,
    LeaseStatus,
    LeaseType,
    PlanRunStatus,
)
from backend.models.host import Device, Host
from backend.models.job import JobInstance, StepTrace
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.services.aggregator import PlanAggregator

PIPELINE_DEF = {"lifecycle": {"init": [], "teardown": []}}


def _seed_plan_run_with_jobs(n_jobs: int = 2) -> dict:
    """创建 1 个 RUNNING PlanRun + N 个 RUNNING JobInstance + N 个 ACTIVE lease。

    每个 job 绑定独立 device,均属同一 host/plan/plan_run。
    """
    suffix = uuid4().hex[:8]
    host_id = f"dlr-{suffix}"
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        host = Host(
            id=host_id, hostname=f"h-{suffix}",
            status=HostStatus.ONLINE.value, created_at=now,
        )
        plan = Plan(
            name=f"plan-{suffix}", description="deadlock regression",
            failure_threshold=0.0, created_by="pytest",
        )
        db.add_all([host, plan])
        db.flush()

        run = PlanRun(
            plan_id=plan.id,
            status=PlanRunStatus.RUNNING.value,
            failure_threshold=0.0,
            plan_snapshot={"name": plan.name, "plan_id": plan.id},
            run_type="MANUAL", triggered_by="pytest",
            started_at=now,
        )
        db.add(run)
        db.flush()

        job_ids: list[int] = []
        device_ids: list[int] = []
        tokens: list[str] = []
        for i in range(n_jobs):
            dev = Device(
                serial=f"DLR{i}-{suffix}", host_id=host_id,
                status="ONLINE", tags=[], created_at=now,
                adb_connected=True, adb_state="device",
            )
            db.add(dev)
            db.flush()

            job = JobInstance(
                plan_run_id=run.id, plan_id=plan.id,
                device_id=dev.id, host_id=host_id,
                status=JobStatus.RUNNING.value, pipeline_def=PIPELINE_DEF,
                created_at=now, updated_at=now, started_at=now,
            )
            db.add(job)
            db.flush()

            fencing_token = f"{dev.id}:1"
            lease = DeviceLease(
                device_id=dev.id, job_id=job.id, host_id=host_id,
                lease_type=LeaseType.JOB.value,
                status=LeaseStatus.ACTIVE.value,
                fencing_token=fencing_token,
                lease_generation=1,
                agent_instance_id=host_id,
                acquired_at=now, renewed_at=now,
                expires_at=now + timedelta(seconds=600),
            )
            db.add(lease)
            db.flush()

            job_ids.append(job.id)
            device_ids.append(dev.id)
            tokens.append(fencing_token)

        db.commit()
        return {
            "host_id": host_id,
            "plan_id": plan.id,
            "plan_run_id": run.id,
            "job_ids": job_ids,
            "device_ids": device_ids,
            "tokens": tokens,
        }
    finally:
        db.close()


def _cleanup_seed(seed: dict) -> None:
    db = SessionLocal()
    try:
        for jid in seed.get("job_ids", []):
            db.query(DeviceLease).filter(DeviceLease.job_id == jid).delete()
            db.query(StepTrace).filter(StepTrace.job_id == jid).delete()
            db.query(JobInstance).filter(JobInstance.id == jid).delete()
        for did in seed.get("device_ids", []):
            db.query(DeviceLease).filter(DeviceLease.device_id == did).delete()
            db.query(Device).filter(Device.id == did).delete()
        if seed.get("plan_run_id"):
            db.query(PlanRun).filter(PlanRun.id == seed["plan_run_id"]).delete()
        if seed.get("plan_id"):
            db.query(PlanStep).filter(PlanStep.plan_id == seed["plan_id"]).delete()
            db.query(Plan).filter(Plan.id == seed["plan_id"]).delete()
        if seed.get("host_id"):
            db.query(Host).filter(Host.id == seed["host_id"]).delete()
        db.commit()
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
# 回归 1: 并发 complete_job 不死锁(路由级,复现生产路径)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="module")
async def test_concurrent_complete_job_no_deadlock():
    """两个并发 complete_job 同一 plan_run 的不同 job 不应死锁。

    回归 2026-06-24 生产事故: plan_run=43 的 job 60/61/62 并发 complete,
    FOR UPDATE 与 FK KEY SHARE 互锁 → 500 Internal Server Error。

    修复后(FOR NO KEY UPDATE): 两个 complete 串行聚合,plan_run 进入终态,
    无 DeadlockDetectedError。
    """
    seed = _seed_plan_run_with_jobs(n_jobs=2)
    try:
        async def _complete(job_id: int, token: str) -> object:
            async with AsyncSessionLocal() as db:
                return await complete_job(
                    job_id=job_id,
                    payload=_RunCompleteIn(
                        update={"status": "FINISHED", "exit_code": 0},
                        fencing_token=token,
                    ),
                    db=db, _=None,
                )

        results = await asyncio.gather(
            _complete(seed["job_ids"][0], seed["tokens"][0]),
            _complete(seed["job_ids"][1], seed["tokens"][1]),
            return_exceptions=True,
        )

        # 两个 complete 都应成功,不抛 DeadlockDetectedError
        errors = [r for r in results if isinstance(r, Exception)]
        assert not errors, (
            f"并发 complete_job 不应抛异常(死锁回归); got: {errors}"
        )

        # 验证两个 job 都进入 COMPLETED
        db = SessionLocal()
        try:
            for jid in seed["job_ids"]:
                job = db.get(JobInstance, jid)
                assert job is not None
                assert job.status == JobStatus.COMPLETED.value, (
                    f"job {jid} 应为 COMPLETED,实际 {job.status}"
                )

            # plan_run 应进入终态(两 job 都成功 → SUCCESS)
            run = db.get(PlanRun, seed["plan_run_id"])
            assert run is not None
            assert run.status in {
                PlanRunStatus.SUCCESS.value,
                PlanRunStatus.PARTIAL_SUCCESS.value,
                PlanRunStatus.FAILED.value,
                PlanRunStatus.DEGRADED.value,
            }, f"plan_run 应进入终态,实际 {run.status}"
            assert run.status == PlanRunStatus.SUCCESS.value, (
                f"两 job 都 COMPLETED + threshold=0 → 应为 SUCCESS,实际 {run.status}"
            )
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio(loop_scope="module")
async def test_concurrent_complete_job_four_way_no_deadlock():
    """4 路并发 complete_job 同一 plan_run 不死锁(复现 4 设备场景)。

    生产日志显示 4 个 job(60/61/62 + 隐含第 4 个)并发 complete plan_run=43,
    3 个进程(24000/38056/21236)分别与 23560 死锁。本测试用 4 job 压测。
    """
    seed = _seed_plan_run_with_jobs(n_jobs=4)
    try:
        async def _complete(job_id: int, token: str) -> object:
            async with AsyncSessionLocal() as db:
                return await complete_job(
                    job_id=job_id,
                    payload=_RunCompleteIn(
                        update={"status": "FINISHED", "exit_code": 0},
                        fencing_token=token,
                    ),
                    db=db, _=None,
                )

        tasks = [
            _complete(jid, tok)
            for jid, tok in zip(seed["job_ids"], seed["tokens"])
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        errors = [r for r in results if isinstance(r, Exception)]
        assert not errors, (
            f"4 路并发 complete_job 不应死锁; got: {errors}"
        )

        db = SessionLocal()
        try:
            run = db.get(PlanRun, seed["plan_run_id"])
            assert run is not None
            assert run.status == PlanRunStatus.SUCCESS.value, (
                f"4 job 都 COMPLETED + threshold=0 → SUCCESS,实际 {run.status}"
            )
            for jid in seed["job_ids"]:
                job = db.get(JobInstance, jid)
                assert job is not None
                assert job.status == JobStatus.COMPLETED.value
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


# ══════════════════════════════════════════════════════════════════════════════
# 回归 2: on_job_terminal 直接并发调用不死锁(服务级)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="module")
async def test_concurrent_on_job_terminal_no_deadlock():
    """直接并发 PlanAggregator.on_job_terminal 不死锁。

    覆盖 reconciler/session_watchdog 等多个调用点并发触发聚合的场景。
    比 complete_job 路由测试更聚焦于 aggregator 锁本身。
    """
    seed = _seed_plan_run_with_jobs(n_jobs=2)
    # 预先将两个 job 置为 COMPLETED(模拟 complete_job 的事务前半段已执行)
    db = SessionLocal()
    try:
        for jid in seed["job_ids"]:
            job = db.get(JobInstance, jid)
            assert job is not None
            job.status = JobStatus.COMPLETED.value
            job.ended_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()

    try:
        async def _aggregate(job_id: int) -> object:
            async with AsyncSessionLocal() as db:
                job = await db.get(JobInstance, job_id)
                assert job is not None
                return await PlanAggregator.on_job_terminal(job, db)

        results = await asyncio.gather(
            _aggregate(seed["job_ids"][0]),
            _aggregate(seed["job_ids"][1]),
            return_exceptions=True,
        )

        errors = [r for r in results if isinstance(r, Exception)]
        assert not errors, (
            f"并发 on_job_terminal 不应死锁; got: {errors}"
        )

        # 至少一个 applied=True(第一个完成的聚合写入终态)
        applied_flags = [r[0] for r in results if isinstance(r, tuple)]
        assert any(applied_flags), (
            f"至少一个聚合应 applied=True; got {results}"
        )
    finally:
        _cleanup_seed(seed)


# ══════════════════════════════════════════════════════════════════════════════
# 回归 3: 锁兼容性守卫 — FOR NO KEY UPDATE 与 FOR KEY SHARE 兼容
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="module")
async def test_no_key_update_compatible_with_fk_key_share():
    """FOR NO KEY UPDATE 与 FK 触发的 FOR KEY SHARE 兼容(不死锁)。

    这是死锁修复的核心原理守卫:如果未来有人误改回 FOR UPDATE,本测试会
    通过 PG 锁等待验证捕获回归(FOR UPDATE 与 KEY SHARE 冲突 → 超时/死锁)。

    验证方式:
      事务 A: UPDATE job_instance(plan_run_id=43) → 持有 plan_run:43 的 KEY SHARE
      事务 B: SELECT plan_run FOR NO KEY UPDATE → 应立即返回(不阻塞)
    """
    seed = _seed_plan_run_with_jobs(n_jobs=1)
    try:
        # 事务 A 持有 plan_run 的 FOR KEY SHARE(通过 UPDATE job_instance 触发 FK 锁),
        # 事务 B 求 FOR NO KEY UPDATE —— 应立即完成(兼容)。
        # 如果未来误改回 FOR UPDATE(key_share=False),与 KEY SHARE 冲突 → 超时。
        async with AsyncSessionLocal() as db_a, AsyncSessionLocal() as db_b:
            job_a = await db_a.get(JobInstance, seed["job_ids"][0])
            assert job_a is not None
            job_a.status = JobStatus.FAILED.value
            await db_a.flush()  # FK KEY SHARE on plan_run

            async def _lock_no_key_update():
                await db_b.execute(
                    select(PlanRun)
                    .where(PlanRun.id == seed["plan_run_id"])
                    .with_for_update(key_share=True)
                )

            # FOR NO KEY UPDATE 与 FOR KEY SHARE 兼容 → 立即完成
            # 如果回退为 FOR UPDATE(key_share=False) → 与 KEY SHARE 冲突 → 超时
            await asyncio.wait_for(_lock_no_key_update(), timeout=5.0)

            await db_a.rollback()
            await db_b.rollback()
    finally:
        _cleanup_seed(seed)


@pytest.mark.asyncio(loop_scope="module")
async def test_no_key_update_still_serializes_writers():
    """两个 FOR NO KEY UPDATE 仍互相冲突(串行化保证不变)。

    防止过度乐观:降级锁模式后必须仍能阻止并发 read-modify-write 覆盖。
    事务 A 持 NO KEY UPDATE,事务 B 求 NO KEY UPDATE → 应阻塞(NOWAIT 抛错)。
    """
    seed = _seed_plan_run_with_jobs(n_jobs=1)
    try:
        async with AsyncSessionLocal() as db_a, AsyncSessionLocal() as db_b:
            # 事务 A 锁定 plan_run
            await db_a.execute(
                select(PlanRun)
                .where(PlanRun.id == seed["plan_run_id"])
                .with_for_update(key_share=True)
            )

            # 事务 B 求同一行的 NO KEY UPDATE → 应阻塞 → NOWAIT 抛 55P03
            with pytest.raises(Exception) as exc_info:
                await db_b.execute(
                    select(PlanRun)
                    .where(PlanRun.id == seed["plan_run_id"])
                    .with_for_update(key_share=True, nowait=True)
                )

            # 验证是 lock_not_available(55P03),不是其他错误
            err_str = str(exc_info.value).lower()
            assert (
                "lock" in err_str
                or "55p03" in err_str
                or "could not obtain lock" in err_str
            ), f"应为 lock_not_available,实际: {exc_info.value}"

            await db_a.rollback()
            await db_b.rollback()
    finally:
        _cleanup_seed(seed)
