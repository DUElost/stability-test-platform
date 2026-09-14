"""#1985 — `abort_plan_run` 必须先锁 PENDING job 行、再锁 `plan_run`。

同一条 (job_instance × plan_run) 行组合上有两处相反顺序：

* **recycler PENDING 超时路径**：`UPDATE job_instance … status='PENDING'`（`recycler.py:849`）
  → 再在 `plan_aggregator_sync` 里 `SELECT PlanRun … FOR NO KEY UPDATE`
  （`job_terminalization.py:205`）。顺序 **job → plan_run**。
* **`abort_plan_run`**：先 `SELECT PlanRun … FOR NO KEY UPDATE`（`:459`）→ 再在
  `_bulk_abort_pending_jobs` 里 `UPDATE job_instance … status='PENDING'`（`:101`）。
  顺序 **plan_run → job**。

两者争用同一批 PENDING 行（回收器的超时候选就是 `status='PENDING'`；abort 的
`pending_ids` 也是），交错即成环。

判据沿用 `#1959`/`#1980`：**不看返回值**，用第三会话 `FOR NO KEY UPDATE NOWAIT`
直接探测「等待方是否已经持有 `plan_run` 行锁」。需要 PostgreSQL。
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
import sqlalchemy.exc
from sqlalchemy import select, text

pytestmark = pytest.mark.skipif(
    os.getenv("DATABASE_URL", "").startswith("sqlite"),
    reason="锁序回归需要 PostgreSQL 行锁（FOR UPDATE / NOWAIT）",
)

from backend.core.database import SessionLocal
from backend.models.device_lease import DeviceLease
from backend.models.enums import HostStatus, JobStatus, PlanRunStatus
from backend.models.host import Device, Host
from backend.models.job import JobArtifact, JobInstance, StepTrace
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun, PlanRunHost
from backend.services.plan_run_abort import abort_plan_run

PIPELINE_DEF = {"lifecycle": {"init": [], "teardown": []}}


def _seed() -> dict:
    """host + device + RUNNING PlanRun + PlanRunHost + 一个 PENDING job。"""
    suffix = uuid4().hex[:8]
    host_id = f"abl-{suffix}"
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        host = Host(
            id=host_id, hostname=f"h-{suffix}",
            status=HostStatus.ONLINE.value, created_at=now,
        )
        plan = Plan(
            name=f"abl-{suffix}", description="abort lock order",
            failure_threshold=0.0, created_by="pytest",
        )
        db.add_all([host, plan])
        db.flush()

        device = Device(
            serial=f"ABL-{suffix}", host_id=host_id, status="ONLINE",
            tags=[], created_at=now, adb_connected=True, adb_state="device",
        )
        db.add(device)
        db.flush()

        run = PlanRun(
            plan_id=plan.id, status=PlanRunStatus.RUNNING.value,
            failure_threshold=0.0,
            plan_snapshot={"name": plan.name, "plan_id": plan.id},
            run_type="MANUAL", triggered_by="pytest", started_at=now,
            total_job_count=1,
        )
        db.add(run)
        db.flush()

        prh = PlanRunHost(
            plan_run_id=run.id, host_id=host_id, device_count=1,
            status="ADMITTED", coordinator_epoch=1, admitted_at=now,
        )
        db.add(prh)
        db.flush()

        job = JobInstance(
            plan_run_id=run.id, plan_id=plan.id, device_id=device.id,
            host_id=host_id, status=JobStatus.PENDING.value,
            pipeline_def=PIPELINE_DEF, created_at=now, updated_at=now,
        )
        db.add(job)
        db.flush()

        seed = {
            "host_id": host_id, "device_id": device.id, "job_id": job.id,
            "plan_id": plan.id, "plan_run_id": run.id, "prh_id": prh.id,
        }
        db.commit()
        return seed
    finally:
        db.close()


def _cleanup(seed: dict) -> None:
    db = SessionLocal()
    try:
        db.execute(text("SET statement_timeout = '15s'"))
        for model, column, key in (
            (DeviceLease, DeviceLease.job_id, "job_id"),
            (StepTrace, StepTrace.job_id, "job_id"),
            (JobArtifact, JobArtifact.job_id, "job_id"),
        ):
            db.query(model).filter(column == seed[key]).delete()
        db.query(JobInstance).filter(JobInstance.id == seed["job_id"]).delete()
        db.query(PlanRunHost).filter(PlanRunHost.id == seed["prh_id"]).delete()
        db.query(PlanRun).filter(PlanRun.id == seed["plan_run_id"]).delete()
        db.query(PlanStep).filter(PlanStep.plan_id == seed["plan_id"]).delete()
        db.query(Plan).filter(Plan.id == seed["plan_id"]).delete()
        db.query(Device).filter(Device.id == seed["device_id"]).delete()
        db.query(Host).filter(Host.id == seed["host_id"]).delete()
        db.commit()
    finally:
        db.close()


def _wait_until_blocked(observer, pid: int, timeout: float = 20.0) -> bool:
    """等到 pid 这条后端因等待行锁而阻塞（``wait_event_type='Lock'``）。"""
    import time as _time

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        row = observer.execute(
            text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
            {"pid": pid},
        ).first()
        if row is not None and row[0] == "Lock":
            return True
        _time.sleep(0.05)
    return False


def test_abort_locks_pending_job_before_plan_run():
    """另一会话持 PENDING job 行锁时，abort_plan_run 不得已持有 plan_run 行锁。

    旧实现先锁 `plan_run` 再批量 UPDATE PENDING 行，因此在这一刻**已经持有**
    `plan_run`；与 recycler 的「持 job 等 plan_run」正好成环。
    """
    seed = _seed()
    blocker = probe = observer = None
    try:
        blocker = SessionLocal()
        probe = SessionLocal()
        observer = SessionLocal()

        # 占住 PENDING job 行（模拟 recycler 的 PENDING 超时路径已锁住该行）
        blocker.execute(
            select(JobInstance)
            .where(JobInstance.id == seed["job_id"])
            .with_for_update()
        )

        outcome: dict = {}
        started = threading.Event()

        def _run_abort() -> None:
            session = SessionLocal()
            try:
                outcome["pid"] = session.execute(
                    text("SELECT pg_backend_pid()")
                ).scalar()
                started.set()
                with patch(
                    "backend.services.plan_run_abort.notify_plan_run_terminal",
                    lambda *a, **k: None,
                ):
                    outcome["result"] = abort_plan_run(
                        seed["plan_run_id"], db=session, reason="lock-order-test",
                        triggered_by="pytest",
                    )
            except BaseException as exc:  # noqa: BLE001 — 交由主线程断言
                outcome["error"] = exc
            finally:
                session.close()

        thread = threading.Thread(target=_run_abort, daemon=True)
        thread.start()
        assert started.wait(timeout=20), "abort 线程未启动"
        assert _wait_until_blocked(observer, outcome["pid"]), (
            "abort_plan_run 应在 PENDING job 行锁上排队（该行被另一会话持有）"
        )

        plan_run_lock_error = None
        try:
            probe.execute(
                select(PlanRun)
                .where(PlanRun.id == seed["plan_run_id"])
                .with_for_update(key_share=True, nowait=True)
            )
        except sqlalchemy.exc.DBAPIError as exc:  # 55P03 lock_not_available
            plan_run_lock_error = exc
        probe.rollback()

        # 放开 job 行锁，让 abort 跑完再断言（避免失败路径把行锁留在半开状态）
        blocker.rollback()
        thread.join(timeout=60)
        assert not thread.is_alive(), "释放 job 行锁后 abort_plan_run 仍未结束"

        assert plan_run_lock_error is None, (
            "abort_plan_run 在等待 PENDING job 行锁时已持有 plan_run 行锁 —— "
            f"锁序仍是 plan_run→job（#1985 回归）：{plan_run_lock_error}"
        )
        assert "error" not in outcome, f"abort 不应报错：{outcome.get('error')!r}"
        assert outcome["result"]["aborted_jobs"] == [seed["job_id"]]

        check = SessionLocal()
        try:
            job = check.get(JobInstance, seed["job_id"])
            assert job is not None
            assert job.status == JobStatus.ABORTED.value
        finally:
            check.close()
    finally:
        for session in (blocker, probe, observer):
            if session is not None:
                try:
                    session.rollback()
                    session.close()
                except Exception:  # noqa: BLE001 — 清理尽力而为
                    pass
        _cleanup(seed)
