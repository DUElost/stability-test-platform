"""#3384：父 Run 终态 `plan_run_status` 的单一发送点守卫（三处旧点删除后）。

ADR-0052 D1 后父终态由聚合者异步判定，`/complete`（`agent_completion`）、
step 状态（`agent_step_status`）、recycler PENDING 超时（`recycler`）三处
「调用返回后立即读父行再广播」在生产语义下恒不命中（dead code），在 TESTING
内联排空下则会与编排者中央补发**双发**。本文件钉住：

1. 三处旧点已删除（静态锚点，防复活）；
2. 三条路径自身不发 `plan_run_status`；
3. `/complete` 路径：默认（内联排空）恰好一次；关闭内联排空（模拟 SAQ 异步）
   时路径不发、由 `drain_plan_run_aggregation_sync` 中央补发恰好一次。
"""

from __future__ import annotations

import ast
import pathlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from backend.api.routes.agent_api import _RunCompleteIn, complete_job
from backend.core.database import AsyncSessionLocal
from backend.models.device_lease import DeviceLease
from backend.models.enums import (
    HostStatus,
    JobStatus,
    LeaseStatus,
    LeaseType,
    PlanRunStatus,
)
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun, PlanRunPendingAggregation

_STALE_SITES = (
    "backend/services/agent_completion.py",
    "backend/services/agent_step_status.py",
    "backend/scheduler/recycler.py",
)


# ── 1. 静态锚点：三处旧点不得复活 ────────────────────────────────────────────


def _tree(rel: str) -> ast.Module:
    p = pathlib.Path(__file__).resolve().parents[3] / rel
    return ast.parse(p.read_text())


def _calls_named(tree: ast.Module, name: str) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == name
    ]


def test_stale_broadcast_sites_removed():
    """旧点若复活：直接 `broadcast_plan_run_status` 或 `schedule_emit("plan_run_status")`。"""
    for rel in _STALE_SITES:
        src = (pathlib.Path(__file__).resolve().parents[3] / rel).read_text()
        assert "broadcast_plan_run_status" not in src, (
            f"{rel} 不得再引用 broadcast_plan_run_status（#3384：中央补发是唯一发送点）"
        )
        for call in _calls_named(_tree(rel), "schedule_emit"):
            assert not (
                call.args and isinstance(call.args[0], ast.Constant)
                and call.args[0].value == "plan_run_status"
            ), f"{rel} 不得再 schedule_emit('plan_run_status')（#3384）"


def test_central_emission_point_present():
    """中央补发点在场：编排者在父终态 commit 后统一发。"""
    tree = _tree("backend/services/plan_run_finalization.py")
    names = {
        n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
    }
    assert "_emit_parent_terminal_status" in names


class _SioSpy:
    """传输层探针：任何 `get_sio().emit(...)` 都会被记录（旧异步发送点也走这里）。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, str | None]] = []

    async def emit(self, event, data=None, **kwargs):  # noqa: ANN001, ARG002
        self.events.append((event, kwargs.get("room")))

    def plan_run_status_count(self) -> int:
        return sum(1 for e, _ in self.events if e == "plan_run_status")


# ── 2/3. /complete 路径 ──────────────────────────────────────────────────────


def _seed_chain(
    db_session,
    *,
    n_jobs: int = 1,
    run_status: str = PlanRunStatus.RUNNING.value,
    job_status: str = JobStatus.RUNNING.value,
) -> dict:
    now = datetime.now(timezone.utc)
    host = Host(
        id="h-3384",
        hostname="h3384",
        status=HostStatus.ONLINE.value,
        ip="10.0.0.51",
        ssh_user="root",
        ssh_port=22,
        extra={"ssh_password": "x"},
        last_heartbeat=now,
    )
    dev = Device(serial="dev-3384", host_id="h-3384", status="BUSY")
    plan = Plan(name="p-3384")
    db_session.add_all([host, dev, plan])
    db_session.commit()

    run = PlanRun(
        plan_id=plan.id,
        status=run_status,
        plan_snapshot={"plan": {"id": plan.id}, "steps": []},
        run_type="MANUAL",
        chain_index=0,
        started_at=now,
        total_job_count=n_jobs,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    out = {"run_id": run.id, "plan_id": plan.id, "job_ids": [], "fencing_tokens": []}
    for _ in range(n_jobs):
        job = JobInstance(
            plan_run_id=run.id,
            plan_id=plan.id,
            device_id=dev.id,
            host_id=host.id,
            status=job_status,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        )
        db_session.add(job)
        db_session.commit()
        db_session.refresh(job)
        token = f"{dev.id}:1"
        db_session.add(DeviceLease(
            device_id=dev.id,
            job_id=job.id,
            host_id=host.id,
            lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=token,
            lease_generation=1,
            agent_instance_id="h-3384",
            acquired_at=now,
            renewed_at=now,
            expires_at=now + timedelta(minutes=10),
        ))
        db_session.commit()
        out["job_ids"].append(job.id)
        out["fencing_tokens"].append(token)
    return out


def _side_effect_patches(inline_drain_off: bool):
    """编排者副作用补丁：通知/链/去重/报告缓存（避免测试触碰外部系统）。"""
    patches = [
        patch("backend.services.notification_service.dispatch_notification_async"),
        patch("backend.services.plan_chain_trigger.trigger_next_plan_sync"),
        patch("backend.services.dedup_scan.should_trigger_dedup", return_value=False),
    ]
    if inline_drain_off:
        # TESTING=1 时该调度自带 skip；关闭 TESTING 后必须显式拦住线程池提交。
        patches.append(
            patch("backend.services.plan_run_finalization.schedule_report_cache_refresh")
        )
    return patches


async def test_complete_path_emits_parent_status_exactly_once(db_session):
    """默认语义（内联排空）：终态波后 `plan_run_status` 恰好一次（旧三处点会双发）。"""
    seed = _seed_chain(db_session)
    import contextlib

    sio = _SioSpy()
    with contextlib.ExitStack() as stack:
        job_push = stack.enter_context(
            patch(
                "backend.services.agent_completion.broadcast_run_job_update",
                new=AsyncMock(),
            )
        )
        emit = stack.enter_context(
            patch("backend.services.plan_run_finalization.emit_plan_run_status")
        )
        stack.enter_context(
            patch("backend.realtime.socketio_server.get_sio", return_value=sio)
        )
        for p in _side_effect_patches(inline_drain_off=False):
            stack.enter_context(p)
        async with AsyncSessionLocal() as adb:
            result = await complete_job(
                job_id=seed["job_ids"][0],
                payload=_RunCompleteIn(
                    update={"status": "FINISHED", "exit_code": 0},
                    fencing_token=seed["fencing_tokens"][0],
                ),
                db=adb,
                _=None,
            )

    assert result.error is None
    job_push.assert_awaited_once()
    emit.assert_called_once()
    assert emit.call_args.args[0] == seed["run_id"]
    assert emit.call_args.args[1] in {"SUCCESS", "PARTIAL_SUCCESS", "FAILED"}
    # 旧实现会经 broadcast_plan_run_status 再从传输层发一次（双发）。
    assert sio.plan_run_status_count() == 0


async def test_complete_path_defers_emit_to_central_drain_when_inline_drain_off(
    db_session, monkeypatch
):
    """关闭内联排空（模拟 SAQ 异步）：路径不发；drain 中央补发恰好一次。"""
    monkeypatch.delenv("TESTING", raising=False)
    seed = _seed_chain(db_session)
    import contextlib

    from backend.services.plan_run_finalization import drain_plan_run_aggregation_sync

    sio = _SioSpy()
    with contextlib.ExitStack() as stack:
        job_push = stack.enter_context(
            patch(
                "backend.services.agent_completion.broadcast_run_job_update",
                new=AsyncMock(),
            )
        )
        emit = stack.enter_context(
            patch("backend.services.plan_run_finalization.emit_plan_run_status")
        )
        stack.enter_context(
            patch("backend.realtime.socketio_server.get_sio", return_value=sio)
        )
        queue = stack.enter_context(patch("backend.core.task_queue.get_queue"))
        queue.return_value.enqueue = AsyncMock()
        for p in _side_effect_patches(inline_drain_off=True):
            stack.enter_context(p)
        async with AsyncSessionLocal() as adb:
            result = await complete_job(
                job_id=seed["job_ids"][0],
                payload=_RunCompleteIn(
                    update={"status": "FINISHED", "exit_code": 0},
                    fencing_token=seed["fencing_tokens"][0],
                ),
                db=adb,
                _=None,
            )

        assert result.error is None
        job_push.assert_awaited_once()
        # 路径自身不发；事实以 pending 标记交付聚合者。
        emit.assert_not_called()
        assert (
            db_session.query(PlanRunPendingAggregation)
            .filter(PlanRunPendingAggregation.plan_run_id == seed["run_id"])
            .count()
            == 1
        )
        # 中央排空（生产由 SAQ 任务/补偿路径触发）→ 恰好一次。
        assert drain_plan_run_aggregation_sync(seed["run_id"]) >= 1

    emit.assert_called_once()
    assert emit.call_args.args[0] == seed["run_id"]
    # 排空前后，传输层都不应出现旧发送点的 plan_run_status（中央补发已被 patch 捕获）。
    assert sio.plan_run_status_count() == 0


# ── step 状态路径 ────────────────────────────────────────────────────────────


async def test_step_path_does_not_self_emit_plan_run_status(db_session):
    """step 路径（`_broadcast_transitioned_jobs`）：只发 job_status，不发父 Run 终态。"""
    seed = _seed_chain(
        db_session,
        run_status=PlanRunStatus.FAILED.value,
        job_status=JobStatus.COMPLETED.value,
    )

    from backend.services.agent_step_status import _broadcast_transitioned_jobs

    sio = _SioSpy()
    with patch(
        "backend.services.agent_step_status.broadcast_run_job_update",
        new=AsyncMock(),
    ) as job_push, patch(
        "backend.services.plan_run_finalization.emit_plan_run_status"
    ) as emit, patch(
        "backend.realtime.socketio_server.get_sio", return_value=sio
    ):
        async with AsyncSessionLocal() as adb:
            await _broadcast_transitioned_jobs(adb, [seed["job_ids"][0]])

    job_push.assert_awaited_once()
    emit.assert_not_called()
    assert sio.plan_run_status_count() == 0


# ── recycler PENDING 超时路径 ────────────────────────────────────────────────


def test_recycler_pending_timeout_does_not_self_emit_plan_run_status(
    db_session, monkeypatch
):
    """recycler 超时路径：父行已终态时只发 job_status，不再补发父 Run 终态。"""
    seed = _seed_chain(
        db_session,
        run_status=PlanRunStatus.FAILED.value,
        job_status=JobStatus.PENDING.value,
    )
    from backend.scheduler import recycler

    events: list[str] = []

    def _rec(name, payload=None, **kwargs):  # noqa: ANN001, ARG001
        events.append(name)

    monkeypatch.setattr(recycler, "schedule_emit", _rec)
    job = db_session.get(JobInstance, seed["job_ids"][0])

    with patch("backend.services.aggregator_sync.plan_aggregator_sync"):
        assert recycler._mark_pending_timeout(
            db_session, job, datetime.now(timezone.utc), "pending_timeout",
        ) is True

    assert "job_status" in events
    assert "plan_run_status" not in events
