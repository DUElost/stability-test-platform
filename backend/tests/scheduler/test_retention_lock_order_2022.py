"""#2022 — 保留清理的加锁顺序必须与 `job → lease → plan_run` 全序一致。

`cron_scheduler.run_retention_cleanup` 原先在候选查询里就
`SELECT … FOR UPDATE SKIP LOCKED`（锁 `plan_run`），随后才 DELETE `device_leases`
与 `job_instance`。而 complete / recycler / reconciler / coordinator-heartbeat
都是 `job → lease → plan_run`。两者争用同一行即成环：

* 热路径事务：持 Job（或 Lease）行锁 → 等 `plan_run` 行锁（终态化聚合）
* 保留清理事务：持 `plan_run` 行锁 → 等 Job 行锁（DELETE `job_instance`）

原先判定「可达性 ≈ 0」的依据是错的：保留期不是数十天而是 **3 天**
（`backend/core/settings/scheduler.py:62` `plan_run_retention_days=3`，ADR-0038 亦载明），
而保留清理事务会在**持有 plan_run 行锁期间**做 NFS 目录回收
（`purge_run_storage_dirs`，`#1521`/`#1698` 的「先文件后行」），持锁窗口不是毫秒级。
于是「终态 run 里的 job 被迟到 / 重复 `/complete` 命中」这类已知形态
（`#743` 幽灵 `/complete` 家族）足以撞上。

本文件钉住不变量（与 `#1959` / `#1985` 同一手法）：让保留清理在**子树行**
（job 或 lease）上被阻塞，此时 `plan_run` 行必须仍可被他人立即锁定——旧实现先锁
`plan_run` 再去等子树行，探测必失败。

需要 PostgreSQL：SQLite 没有行锁冲突检测，观察不到锁等待。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import sqlalchemy.exc
from sqlalchemy import select, text
from sqlalchemy.orm import Session

# #2116：需要 PostgreSQL 行锁（FOR UPDATE / NOWAIT）。本仓 harness 总是提供 PG（conftest 的
# testcontainers / CI 的 PG service），无 PG 时在 conftest 阶段就报错——**刻意不写**「非 PG
# 就 skip」的分支：它在 conftest 覆盖 DATABASE_URL 之后不可达，只会把环境问题变成静默跳过。

from backend.core.database import SessionLocal
from backend.models.device_lease import DeviceLease
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.scheduler import cron_scheduler

PIPELINE_DEF = {"lifecycle": {"init": [], "teardown": []}}


def _seed() -> dict:
    """1 host + 1 device + 1 **终态且过期** run + 1 job + 1 lease。

    run 取 `started_at = now - 10d`：无论 `plan_run_retention_days` 取默认 3 还是
    测试覆写值，都在保留期之外；且没有任何 run 引用它（`_retention_safe_ids` 的
    引用闭包不会把它保留下来）。
    """
    suffix = uuid4().hex[:8]
    host_id = f"ret-{suffix}"
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        host = Host(
            id=host_id, hostname=f"h-{suffix}",
            status=HostStatus.ONLINE.value, created_at=now,
        )
        plan = Plan(
            name=f"ret-{suffix}", description="retention lock order regression",
            failure_threshold=0.0, created_by="pytest",
        )
        db.add_all([host, plan])
        db.flush()

        device = Device(
            serial=f"RET-{suffix}", host_id=host_id, status="ONLINE",
            tags=[], created_at=now, adb_connected=True, adb_state="device",
        )
        db.add(device)
        db.flush()

        run = PlanRun(
            plan_id=plan.id, status="SUCCESS", failure_threshold=0.0,
            plan_snapshot={"name": plan.name, "plan_id": plan.id},
            run_type="MANUAL", triggered_by="pytest",
            started_at=now - timedelta(days=10),
        )
        db.add(run)
        db.flush()

        job = JobInstance(
            plan_run_id=run.id, plan_id=plan.id, device_id=device.id,
            host_id=host_id, status=JobStatus.COMPLETED.value,
            pipeline_def=PIPELINE_DEF, created_at=now, updated_at=now,
            started_at=now,
        )
        db.add(job)
        db.flush()

        lease = DeviceLease(
            device_id=device.id, job_id=job.id, host_id=host_id,
            lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=f"{device.id}:1", lease_generation=1,
            agent_instance_id=host_id, acquired_at=now, renewed_at=now,
            expires_at=now + timedelta(seconds=60),
        )
        db.add(lease)
        db.commit()
        return {
            "plan_run_id": run.id,
            "job_id": job.id,
            "lease_id": lease.id,
        }
    finally:
        db.close()


def _wait_lock_waiter(observer, timeout: float = 20.0) -> bool:
    """等到**有会话在等锁**（`pg_locks.granted = false`）。

    判据为什么不是「按关系名匹配」：**等一行**（`FOR UPDATE` / `DELETE` 撞上他人持有）
    在 PG 里表现为等待对方的 ``transactionid``，该 `pg_locks` 行的 relation **为
    NULL**——按 relation 关联 `pg_class` 永远匹配不到（实测 20s 假红）。
    也不能退而求其次去读 `pg_stat_activity.query` 的文本：阻塞会话显示的可能是
    **上一条**语句（实测：等待 `transactionid ShareLock` 时 query 仍是上一条
    `plan_run` 查询），按表名匹配同样假红。

    所以只判「存在未获授的锁」，另加「属于别的会话」以避免自我匹配。
    """
    deadline = time.monotonic() + timeout
    stmt = text(
        "SELECT pid FROM pg_locks WHERE NOT granted AND pid <> pg_backend_pid()"
    )
    while time.monotonic() < deadline:
        if observer.execute(stmt).all():
            return True
        time.sleep(0.05)
    return False


def _probe_plan_run_lock(conn, run_id: int) -> Exception | None:
    """第三会话尝试立即锁定 plan_run 行；返回异常表示「已被他人持有」。"""
    try:
        conn.execute(
            select(PlanRun.id)
            .where(PlanRun.id == run_id)
            .with_for_update(nowait=True)
        )
        return None
    except sqlalchemy.exc.DBAPIError as exc:  # NOWAIT 命中 55P03
        return exc
    finally:
        conn.rollback()


@pytest.mark.parametrize(
    ("relation", "lock_stmt"),
    [
        ("job_instance", "job"),
        ("device_leases", "lease"),
    ],
)
def test_retention_locks_subtree_before_plan_run(
    engine, monkeypatch, tmp_path, relation, lock_stmt,
):
    """清理在子树行上排队时，不得已持有 plan_run 行锁（#2022 主断言）。"""
    from backend.realtime import log_writer

    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path / "retention-storage"))
    monkeypatch.setattr(log_writer, "LOG_BASE_DIR", tmp_path / "console")
    # 清理用模块级 SessionLocal；换成绑定同一 engine 的新会话，便于线程内独立连接。
    monkeypatch.setattr(cron_scheduler, "SessionLocal", lambda: Session(bind=engine))

    seed = _seed()

    blocker = engine.connect()
    tx = blocker.begin()
    if lock_stmt == "job":
        blocker.execute(
            select(JobInstance.id)
            .where(JobInstance.id == seed["job_id"])
            .with_for_update()
        )
    else:
        blocker.execute(
            select(DeviceLease.id)
            .where(DeviceLease.id == seed["lease_id"])
            .with_for_update()
        )

    worker = threading.Thread(target=cron_scheduler.run_retention_cleanup, daemon=True)
    worker.start()

    observer = engine.connect()
    try:
        assert _wait_lock_waiter(observer), (
            f"保留清理未进入锁等待（{relation} 阻塞点没出现）——本用例失去意义"
        )
        # 关键断言：此刻 plan_run 行必须仍可被他人立即锁定。
        # 结果先存起来、不让断言在中途抛——否则清理线程会带着行锁被留在半开状态，
        # 清理阶段与它互等而挂住（掩盖真正的失败原因）。
        probe_error = _probe_plan_run_lock(observer, seed["plan_run_id"])
    finally:
        tx.rollback()
        blocker.close()
        worker.join(timeout=30)
        observer.close()

    assert probe_error is None, (
        f"保留清理在为 {relation} 行加锁排队时已持有 plan_run 行锁 —— "
        f"锁序仍是 plan_run → job/lease（#2022 回归）：{probe_error}"
    )
    assert not worker.is_alive(), "放开子树锁后保留清理仍未结束"

    check = Session(bind=engine)
    try:
        # 修复只调顺序、不改语义：该终态 run 仍应被删除。
        assert check.get(PlanRun, seed["plan_run_id"]) is None, (
            "保留清理应删除该终态 run（#2022 不得改变删除语义）"
        )
    finally:
        check.close()
