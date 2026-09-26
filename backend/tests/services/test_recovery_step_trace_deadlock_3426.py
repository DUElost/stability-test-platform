"""#3426：recovery/sync 与 step_trace 插入的 job 行锁死锁回归守卫。

实证形态（2026-09-26 18:45:46，中止回流窗口）：`recovery/sync` 在循环里对
`job_instance` 取 **FOR UPDATE** 并持有到函数末尾才提交；`step_trace` 的 INSERT
经外键对同一 job 行取 **FK KEY SHARE**。两侧各按自己的多行顺序取锁，交错即成环
（PG 日志：`SELECT job_instance … FOR UPDATE` ↔ `INSERT INTO step_trace …`）。

修复 = job 行锁降为 **FOR NO KEY UPDATE**（`key_share=True`，与 KEY SHARE 兼容；
`sync_agent_recovery` 不写 job 的任何键列，语义不受影响）。

守卫分两层：
1. 静态锚点：该 select 必须带 `key_share=True`，且不得回退为裸 `with_for_update()`；
2. 真实 PG 并发：A = 真实 `sync_agent_recovery`（升序持 j1→j2、末尾提交），
   B = 真实 `reconcile_step_traces`（按 trace 顺序取 FK KEY SHARE：j2→j1）。
   交错精确编排：A 在首个 job 的 `on_job_terminal` 处暂停（此时已持 j1 锁）→
   B 插入 j2 后通知 → A 继续。旧代码在此成环（DeadlockDetected），新代码无冲突。
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select

from backend.core.database import AsyncSessionLocal
from backend.models.device_lease import DeviceLease
from backend.models.enums import (
    HostStatus,
    JobStatus,
    LeaseStatus,
    LeaseType,
)
from backend.models.host import Device, Host
from backend.models.job import JobInstance, StepTrace
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services.agent_recovery import _RecoverySyncIn, sync_agent_recovery
from backend.services.reconciler import reconcile_step_traces

_SRC = pathlib.Path(__file__).resolve().parents[3] / "backend/services/agent_recovery.py"


# ── 1. 静态锚点：job 行锁必须是 FOR NO KEY UPDATE ─────────────────────────────


def _job_lock_call(fn: ast.AsyncFunctionDef) -> ast.Call | None:
    """在 `sync_agent_recovery` 里找挂在 `select(JobInstance)` 链上的取锁调用。"""
    for node in ast.walk(fn):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "with_for_update"
        ):
            continue
        cur: ast.AST | None = node.func.value
        while isinstance(cur, ast.Call):
            if (
                isinstance(cur.func, ast.Name)
                and cur.func.id == "select"
                and cur.args
                and isinstance(cur.args[0], ast.Name)
                and cur.args[0].id == "JobInstance"
            ):
                return node
            cur = cur.func.value if isinstance(cur.func, ast.Attribute) else None
    return None


def test_recovery_job_lock_is_no_key_update():
    tree = ast.parse(_SRC.read_text())
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "sync_agent_recovery"
    )
    lock = _job_lock_call(fn)
    assert lock is not None, "sync_agent_recovery 里找不到 job 行锁 select"
    kw = {k.arg: getattr(k.value, "value", None) for k in lock.keywords}
    assert kw.get("key_share") is True, (
        "job 行锁必须带 key_share=True（FOR NO KEY UPDATE）——FOR UPDATE 会与 "
        "step_trace 的 FK KEY SHARE 成环（#3426）"
    )


def test_recovery_has_no_plain_for_update_on_job():
    """防回退：不得再出现「select(JobInstance) … .with_for_update()」裸形态。"""
    seg = _SRC.read_text()
    assert not re.search(
        r"select\(JobInstance\)[\s\S]{0,160}?\.with_for_update\(\)", seg
    ), "job 行锁回退为 FOR UPDATE（#3426 会复现）"


# ── 2. 真实 PG 并发：旧锁形态必死锁，新锁形态无冲突 ─────────────────────────


def _seed(db_session) -> dict:
    now = datetime.now(timezone.utc)
    host = Host(
        id="h-3426",
        hostname="h3426",
        status=HostStatus.ONLINE.value,
        ip="10.0.0.62",
        ssh_user="root",
        ssh_port=22,
        extra={"ssh_password": "x"},
        last_heartbeat=now,
        boot_id="boot-old",
    )
    plan = Plan(name="p-3426")
    db_session.add_all([host, plan])
    db_session.commit()
    run = PlanRun(
        plan_id=plan.id,
        status="RUNNING",
        plan_snapshot={"plan": {"id": plan.id}, "steps": []},
        run_type="MANUAL",
        chain_index=0,
        started_at=now,
        total_job_count=2,
    )
    db_session.add(run)
    db_session.commit()

    devices, jobs, tokens = [], [], []
    for i in (1, 2):
        dev = Device(serial=f"dev-3426-{i}", host_id="h-3426", status="BUSY")
        db_session.add(dev)
        db_session.commit()
        job = JobInstance(
            plan_run_id=run.id,
            plan_id=plan.id,
            device_id=dev.id,
            host_id="h-3426",
            status=JobStatus.RUNNING.value,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        )
        db_session.add(job)
        db_session.commit()
        token = f"{dev.id}:1"
        db_session.add(DeviceLease(
            device_id=dev.id,
            job_id=job.id,
            host_id="h-3426",
            lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=token,
            lease_generation=1,
            agent_instance_id="agent-old",
            acquired_at=now,
            renewed_at=now,
            expires_at=now + timedelta(minutes=10),
        ))
        db_session.commit()
        devices.append(dev)
        jobs.append(job)
        tokens.append(token)
    return {"run": run, "devices": devices, "jobs": jobs, "tokens": tokens}


@pytest.mark.asyncio(loop_scope="module")
async def test_recovery_sync_and_step_trace_insert_do_not_deadlock(db_session):
    seed = _seed(db_session)
    j1, j2 = seed["jobs"][0].id, seed["jobs"][1].id
    payload = _RecoverySyncIn(
        host_id="h-3426",
        agent_instance_id="agent-new",
        boot_id="boot-new",  # ≠ host.boot_id ⇒ 走 boot_mismatch 清理分支（逐 job 取锁）
        active_jobs=[
            {
                "job_id": j1, "device_id": seed["devices"][0].id,
                "device_serial": seed["devices"][0].serial,
                "fencing_token": seed["tokens"][0],
            },
            {
                "job_id": j2, "device_id": seed["devices"][1].id,
                "device_serial": seed["devices"][1].serial,
                "fencing_token": seed["tokens"][1],
            },
        ],
    )

    first_locked = asyncio.Event()
    release = asyncio.Event()
    calls = {"n": 0}

    async def _pause_on_first(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            first_locked.set()
            await release.wait()

    now_iso = datetime.now(timezone.utc).isoformat()
    traces = [
        {  # 先插 j2（旧代码：A 此时不持 j2，KEY SHARE 先拿到）
            "job_id": j2, "step_id": "s1", "stage": "execute",
            "event_type": "status_update", "status": "RUNNING",
            "trace_event_id": f"t3426-{j2}-1", "original_ts": now_iso,
        },
        {  # 再插 j1（旧代码：被 A 的 FOR UPDATE 挡住 → 成环）
            "job_id": j1, "step_id": "s1", "stage": "execute",
            "event_type": "status_update", "status": "RUNNING",
            "trace_event_id": f"t3426-{j1}-1", "original_ts": now_iso,
        },
    ]
    first_inserted = asyncio.Event()

    async def _run_recovery():
        async with AsyncSessionLocal() as adb:
            return await sync_agent_recovery(adb, payload)

    async def _run_traces():
        async with AsyncSessionLocal() as bdb:
            original_execute = bdb.execute
            state = {"n": 0}

            async def _spy(stmt, *args, **kwargs):  # noqa: ANN001, ANN202
                result = await original_execute(stmt, *args, **kwargs)
                state["n"] += 1
                if state["n"] == 1:
                    first_inserted.set()
                return result

            bdb.execute = _spy  # type: ignore[method-assign]
            return await reconcile_step_traces("agent", traces, bdb)

    with patch(
        "backend.services.agent_recovery.PlanAggregator.on_job_terminal",
        new=_pause_on_first,
    ):
        task_a = asyncio.create_task(_run_recovery())
        await asyncio.wait_for(first_locked.wait(), timeout=5)
        task_b = asyncio.create_task(_run_traces())
        await asyncio.wait_for(first_inserted.wait(), timeout=5)
        release.set()
        results = await asyncio.wait_for(
            asyncio.gather(task_a, task_b, return_exceptions=True), timeout=15,
        )

    errors = [r for r in results if isinstance(r, BaseException)]
    assert not errors, f"recovery/sync 与 step_trace 插入不应死锁，得到: {errors!r}"
    actions = results[0]["actions"]
    assert len(actions) == 2 and all(a["action"] == "CLEANUP" for a in actions)

    db_session.expire_all()
    stored = db_session.execute(
        select(StepTrace).where(StepTrace.job_id.in_([j1, j2]))
    ).scalars().all()
    assert len(stored) == 2
    assert db_session.get(JobInstance, j1).status == JobStatus.FAILED.value
    assert db_session.get(JobInstance, j2).status == JobStatus.FAILED.value
