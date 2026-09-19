"""#2787 — 租约回收器 check 2（stale UNKNOWN）未下沉「一候选一事务边界」：锁序回归。

`#2635` 只把显式提交落在 check 1 `_reconcile_expired_leases`；同文件 check 2
`_reconcile_stale_unknown_jobs` 保留同一形状的退化路径——`on_job_terminal` 只在
`applied=True`（`terminal_job_count >= total_job_count`，仅**末位**候选成立）时
自行提交。于是从第 2 条候选起，同一事务在**已持 `plan_run` 行锁**的情况下再取 job
行锁：

* 回收事务（修复前）：`plan_run` → `job`
* `complete_agent_job`  ：`job` → `plan_run`（I2 基准）

⇒ 可成环。本文件钉住修复后的不变量：**候选 #1 的终态须在候选 #2 取 job 行锁之前
提交**（等价地：候选 #1 的 `plan_run` 行锁不得跨到候选 #2）。

seed 必须用生产形状：一个 PlanRun 挂 ≥2 个 job 且 `total_job_count = len(jobs)`；
且形态必须是「UNKNOWN、`ended_at` 已过宽限、**无 ACTIVE 租约**」——这是 check 2 的
扫描条件（check 1 的 Phase 2 需租约在场，走不到本 check 的路径）。

判据用阻塞法定死：另一会话占住**最后一条**候选的 job 行 ⇒ 回收器做完前两条后
必然停在第三条上。此时——

* 修复后：候选 #1 已显式提交 ⇒ 观察会话读到 FAILED、可立即锁 `plan_run` 行；
* 修复前：候选 #1 的变更还在未提交事务里 ⇒ 观察会话读到 UNKNOWN，且 `plan_run`
  行锁被该事务持有着。

需要 PostgreSQL：SQLite 既无行锁冲突检测，也观察不到锁等待。
"""

from __future__ import annotations

import asyncio

from datetime import datetime, timedelta, timezone

import sqlalchemy.exc
from sqlalchemy import select, text

from backend.core.database import AsyncSessionLocal, SessionLocal, async_engine
from backend.models.enums import HostStatus, JobStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.scheduler.device_lease_reconciler import _reconcile_stale_unknown_jobs

PIPELINE_DEF = {"lifecycle": {"init": [{"script": "dummy"}], "teardown": []}}


def _seed_stale_unknown_run(host_id: str, n_jobs: int) -> dict:
    """生产形状：一个 PlanRun 挂 ``n_jobs`` 个 stale-UNKNOWN job（**无租约**）。"""
    now = datetime.now(timezone.utc)
    past = now - timedelta(seconds=7200)          # 远超 UNKNOWN grace
    db = SessionLocal()
    try:
        host = Host(id=host_id, hostname=f"h-{host_id}",
                    status=HostStatus.ONLINE.value, created_at=now)
        plan = Plan(name=f"wf-{host_id}", description="lock order (#2787)",
                    created_by="pytest")
        db.add_all([host, plan])
        db.flush()
        db.add(PlanStep(plan_id=plan.id, step_key="default", script_name="dummy",
                        script_version="v1.0.0", stage="init", sort_order=0))
        run = PlanRun(plan_id=plan.id, status="RUNNING", triggered_by="pytest",
                      plan_snapshot={"name": plan.name, "plan_id": plan.id},
                      run_type="MANUAL", started_at=now,
                      total_job_count=n_jobs)      # ← 生产形状的关键字段
        db.add(run)
        db.flush()

        jobs = []
        for i in range(n_jobs):
            device = Device(id=9300 + i, serial=f"DW-SU-{i}", host_id=host_id,
                            status="ONLINE", tags=[], created_at=now,
                            adb_connected=True, adb_state="device")
            db.add(device)
            db.flush()
            # check 2 的扫描条件：UNKNOWN + ended_at 已过宽限 + 租约已不存在。
            job = JobInstance(
                plan_run_id=run.id, plan_id=plan.id, device_id=device.id,
                host_id=host_id, status=JobStatus.UNKNOWN.value,
                pipeline_def=PIPELINE_DEF, created_at=past, updated_at=past,
                started_at=past, ended_at=past,
            )
            db.add(job)
            db.flush()
            jobs.append(job)
        db.commit()
        return {"run_id": run.id, "job_ids": [j.id for j in jobs]}
    finally:
        db.close()


async def _session_pid(db) -> int:
    return (await db.execute(text("SELECT pg_backend_pid()"))).scalar()


async def _wait_until_blocked(observer, pid: int, timeout: float = 15.0) -> bool:
    """等到 pid 这条后端因等待行锁而阻塞（``wait_event_type='Lock'``）。"""
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


async def _probe_locked(observer, stmt) -> Exception | None:
    """用 NOWAIT 试锁，返回异常表示「别人已持有该行锁」。"""
    try:
        await observer.execute(stmt)
    except sqlalchemy.exc.DBAPIError as exc:  # 55P03 lock_not_available
        return exc
    finally:
        await observer.rollback()
    return None


async def test_stale_unknown_commits_candidate_before_next_job_lock():
    """#2787 主判据：候选 #1 的终态与 plan_run 行锁都不得跨到候选 #2。

    阻塞法把时序钉死：占住末位候选的 job 行 ⇒ 回收器在候选 #3 处排队；此刻
    检查候选 #1 是否**已提交**（读到 FAILED）且其 `plan_run` 行锁**已释放**
    （NOWAIT 试锁成功）。
    """
    seed = _seed_stale_unknown_run("host-su-1", 3)

    await async_engine.dispose()
    async with (
        AsyncSessionLocal() as db_rec,
        AsyncSessionLocal() as db_blocker,
        AsyncSessionLocal() as db_obs,
    ):
        await db_blocker.execute(
            select(JobInstance)
            .where(JobInstance.id == seed["job_ids"][-1])
            .with_for_update()
        )

        rec_pid = await _session_pid(db_rec)
        task = asyncio.create_task(_reconcile_stale_unknown_jobs(db_rec))
        try:
            assert await _wait_until_blocked(db_obs, rec_pid), (
                "回收器应停在末位候选的 job 行锁上（该行被另一会话持有）——"
                "若未阻塞，说明前序候选没有走完，判据失去时序锚点"
            )

            # 判据一：候选 #1 的终态已提交（修复前在同一未提交事务里 → UNKNOWN）。
            job1 = (await db_obs.execute(
                select(JobInstance).where(JobInstance.id == seed["job_ids"][0])
            )).scalar_one()
            assert job1.status == JobStatus.FAILED.value, (
                "候选 #1 应已提交终态（#2787：一候选一事务边界），"
                f"实际 {job1.status} —— 其变更仍滞留于跨候选事务"
            )

            # 判据二：候选 #1 的 plan_run 行锁已释放（修复前被持有到 check 结束）。
            run_lock_error = await _probe_locked(db_obs, (
                select(PlanRun)
                .where(PlanRun.id == seed["run_id"])
                .with_for_update(nowait=True)
            ))
            assert run_lock_error is None, (
                "plan_run 行锁在候选循环中未释放（#2787）：回收器停在候选 #3 时仍"
                f"持有该行锁 ⇒ 锁序 plan_run→job，可与 job→plan_run 成环：{run_lock_error}"
            )
        finally:
            await db_blocker.rollback()
            result = await asyncio.wait_for(task, timeout=15)

        assert result == 3, f"阻塞解除后应排空全部 3 条候选，实际 {result}"
        for job_id in seed["job_ids"]:
            status = (await db_obs.execute(
                select(JobInstance.status).where(JobInstance.id == job_id)
            )).scalar_one()
            assert status == JobStatus.FAILED.value
