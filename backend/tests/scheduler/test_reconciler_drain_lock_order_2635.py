"""#2635 — 租约回收器把 plan_run 行锁带进候选循环：锁序回归。

`#2531` 为提升解锁速率把终态化搬进候选循环，并声明「一候选一事务边界」。但该边界
**只在末位候选成立**：`on_job_terminal` 的 `commit()` 位于
终态编排（原 `_post_aggregation_side_effects_async`，#3299 后为 `plan_run_finalization.finalize_parent_run_async`）的 `applied=True` 分支，而
`plan_run_aggregation` 要求 `terminal_job_count >= total_job_count` 才 `applied`。

于是从第 2 条候选起，同一事务在**已持 `plan_run` 行锁**的情况下再去锁 `job_instance`：

* 回收事务（修复前）：`plan_run` → `job`
* `complete_agent_job`  ：`job` → `plan_run`（I2 基准）

⇒ 可成环。本文件钉住修复后的不变量：**候选 #1 的 plan_run 行锁须在候选 #2 取
job 行锁之前释放**。

**seed 必须用生产形状**（一个 PlanRun 挂 ≥2 个 job 且 `total_job_count = len(jobs)`）：
既有 `test_device_lease_reconciler.py::_seed()` 每次新建一个 `Plan` ⇒ 一 job 一 PlanRun
⇒ `total = 1` ⇒ 每候选都 `applied=True`、每候选都真提交 ⇒ 那条「独立提交」用例
**恒绿**、走不到退化路径（#2635 正文实测）。

需要 PostgreSQL：SQLite 无行锁冲突检测。
"""

from __future__ import annotations

import asyncio

from datetime import datetime, timedelta, timezone

import sqlalchemy.exc
from sqlalchemy import select, text

from backend.core.database import AsyncSessionLocal, SessionLocal, async_engine
from backend.models.device_lease import DeviceLease
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.scheduler.device_lease_reconciler import _reconcile_expired_leases

PIPELINE_DEF = {"lifecycle": {"init": [{"script": "dummy"}], "teardown": []}}


def _seed_multi_job_run(host_id: str, n_jobs: int) -> dict:
    """**生产形状**：一个 PlanRun 挂 ``n_jobs`` 个 job，``total_job_count = n_jobs``。

    这是与既有 `_seed()`（一 job 一 PlanRun）的关键区别——只有多 job 共享 run 时，
    非末位候选才不 `applied`、才走得到「未提交即进入下一候选」的退化路径。
    """
    now = datetime.now(timezone.utc)
    past = now - timedelta(seconds=7200)
    db = SessionLocal()
    try:
        host = Host(id=host_id, hostname=f"h-{host_id}",
                    status=HostStatus.ONLINE.value, created_at=now)
        plan = Plan(name=f"wf-{host_id}", description="lock order (#2635)",
                    created_by="pytest")
        db.add_all([host, plan])
        db.flush()
        db.add(PlanStep(plan_id=plan.id, step_key="default", script_name="dummy",
                        script_version="v1.0.0", stage="init", sort_order=0))
        run = PlanRun(plan_id=plan.id, status="RUNNING", triggered_by="pytest",
                      plan_snapshot={"name": plan.name, "plan_id": plan.id},
                      run_type="MANUAL", started_at=now,
                      total_job_count=n_jobs)          # ← 生产形状的关键字段
        db.add(run)
        db.flush()

        jobs, leases = [], []
        for i in range(n_jobs):
            device = Device(id=9000 + i, serial=f"DW-LO-{i}", host_id=host_id,
                            status="ONLINE", tags=[], created_at=now,
                            adb_connected=True, adb_state="device")
            db.add(device)
            db.flush()
            # 必须 UNKNOWN 且 ended_at **已过宽限**：Phase 2（释放+终态化）只对
            # 这一形态生效（RUNNING 只走 Phase 1 的 RUNNING→UNKNOWN，不触发终态化，
            # 也就走不到 #2635 的 plan_run 锁路径）。
            job = JobInstance(
                plan_run_id=run.id, plan_id=plan.id, device_id=device.id,
                host_id=host_id, status=JobStatus.UNKNOWN.value,
                pipeline_def=PIPELINE_DEF, created_at=past, updated_at=past,
                started_at=past,
                ended_at=past,                        # 早于宽限截止 → 可终态化
            )
            db.add(job)
            db.flush()
            # ACTIVE 且已过期的 JOB 租约（回收器 Phase 1 会把它置 UNKNOWN）
            lease = DeviceLease(
                device_id=device.id, job_id=job.id, host_id=host_id,
                lease_type=LeaseType.JOB.value, status=LeaseStatus.ACTIVE.value,
                fencing_token=1, lease_generation=1,
                agent_instance_id=f"inst-{i}",        # NOT NULL
                acquired_at=past, renewed_at=past,
                expires_at=past,                      # 已过期
            )
            db.add(lease)
            db.flush()
            jobs.append(job)
            leases.append(lease)
        db.commit()
        return {
            "run_id": run.id,
            "job_ids": [j.id for j in jobs],
            "lease_ids": [l.id for l in leases],
        }
    finally:
        db.close()


async def test_plan_run_lock_not_held_across_candidates():
    """#2635 主判据（**锁**维度）：候选 #1 的 plan_run 行锁不得跨到候选 #2。

    判据用 `pg_locks` 实测：让回收器处理多候选，同时由观察会话尝试
    `SELECT ... FOR NO KEY UPDATE` 候选 #1 的 plan_run 行——
    - 修复后：候选 #1 已提交 → **立即可锁**；
    - 修复前：该行的锁被同一事务持有到 check 结束 → 观察会话**阻塞**。

    用 `lock_timeout` 把「阻塞」变成可断言的错误，避免测试挂死。
    """
    seed = _seed_multi_job_run("host-lo-1", 3)
    run_id = seed["run_id"]

    await async_engine.dispose()
    async with AsyncSessionLocal() as db_rec:
        async with AsyncSessionLocal() as db_obs:
            # 观察会话设短锁超时：拿不到即失败（而非长时间等待）
            await db_obs.execute(text("SET lock_timeout = '2s'"))
            task = asyncio.create_task(_reconcile_expired_leases(db_rec))
            await asyncio.sleep(0.3)      # 让候选 #1 走完（或被阻塞）

            locked = False
            try:
                await db_obs.execute(
                    select(PlanRun).where(PlanRun.id == run_id).with_for_update()
                )
                locked = True
            except sqlalchemy.exc.OperationalError:
                locked = False

            assert locked, (
                "plan_run 行锁在候选循环中未释放（#2635）：候选 #1 完成后 "
                "观察会话仍拿不到该行锁 ⇒ 锁序 plan_run→job，可与 job→plan_run 成环"
            )
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


async def test_each_candidate_commits_independently_with_multi_job_run():
    """#2635 判据二：**多 job 共享 run** 时仍是「一候选一事务」而非尾部批量。

    既有 `test_reconciler_phase2_commits_each_candidate_independently` 因 seed
    是一 job 一 PlanRun（`total=1`）而**恒绿**——它走不到「非末位候选不 applied」
    的路径。本用例用生产形状把该路径真正打开。
    """
    seed = _seed_multi_job_run("host-lo-2", 2)

    await async_engine.dispose()
    async with AsyncSessionLocal() as db_rec:
        # 用另一会话占住**最后一条**候选的租约行 → 回收器必然先做完前一条才排队
        async with AsyncSessionLocal() as db_blocker:
            await db_blocker.execute(
                select(DeviceLease)
                .where(DeviceLease.id == seed["lease_ids"][-1])
                .with_for_update()
            )
            # 回收器在独立事务里跑；它在最后那条候选上会阻塞
            task = asyncio.create_task(_reconcile_expired_leases(db_rec))
            await asyncio.sleep(0.5)      # 让候选 #1 走完

            # 关键断言：候选 #1 的终态**已提交**（观察会话可读）——修复前它在
            # 未提交事务里，此处会读到 RUNNING/ACTIVE。
            async with AsyncSessionLocal() as db_obs:
                job1 = (await db_obs.execute(
                    select(JobInstance).where(JobInstance.id == seed["job_ids"][0])
                )).scalar_one()
                lease1 = (await db_obs.execute(
                    select(DeviceLease).where(DeviceLease.id == seed["lease_ids"][0])
                )).scalar_one()
                assert job1.status == JobStatus.FAILED.value, (
                    f"候选 #1 应已提交终态（#2635：一候选一事务边界），实际 {job1.status}"
                )
                assert lease1.status == LeaseStatus.RELEASED.value, (
                    f"候选 #1 租约应已提交释放，实际 {lease1.status}"
                )
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
