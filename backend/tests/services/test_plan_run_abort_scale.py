"""#703 ①：500 job / 30 host 规模的 abort 回归——「控制面保持可响应」的可复现判据。

#327 现场（~497 job / 30 host，2026-09-01）不是「跑得慢」：逐 job 的进度 emit、逐
host 的控制扇出、逐 job 的 DB 往返，任一回归都会把主事件循环与连接池吃满 →
`/auth/refresh` 拿不到连接 → 前端按 401 清会话跳 `/login`。那条链上的修复散在
#492/#988（批量终态化）、#703（扇出合并、emit 折叠、持锁时长）几单里，本文件把它们
**在规模上**钉住。

判据因此**不是耗时**（机器相关、随时间漂移），而是四条规模不变性：

1. **SQL 语句数不随 job 数线性增长**：同为 30 host，30 job 与 510 job 的语句数之差
   必须是常数级——逐 job 往返回归时这里差出数百条；
2. **控制扇出恰好一次**：`schedule_agent_control_fanout` 一次收齐所有 host 的 control
   items（合并前的形态是每 host 一次 `run_coroutine_threadsafe`）；
3. **进度推送是汇总而非逐 job**：`job_status` 只发一条 `abort_bulk=True`；
4. **作用域与数量如实**：扇出样本恰好一条 `("run", 510)`——scope 错位（#1880）或漏算
   RUNNING 那一类都会在这里现形。

需要 PostgreSQL（conftest 的 testcontainers / CI 的 PG service）。按仓库口径**刻意
不写**「非 PG 就 skip」的分支：那会让环境问题静默变成一条假绿。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import event, insert, select, text

from backend.core.database import SessionLocal, engine
from backend.models.device_lease import DeviceLease
from backend.models.enums import HostStatus, JobStatus, PlanRunStatus
from backend.models.host import Device, Host
from backend.models.job import JobArtifact, JobInstance, StepTrace
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun, PlanRunHost
from backend.services.plan_run_abort import abort_plan_run

PIPELINE_DEF = {"lifecycle": {"init": [], "teardown": []}}

#: #327 的规模：~500 job / 30 host。17×30 = 510。
HOSTS = 30
JOBS_PER_HOST = 17
RUNNING_PER_HOST = 6

#: 30 host 不变时，「30 job → 510 job」允许的语句数增量上界（常数级，留 ~2× 余量）。
#: 逐 job 往返的回归会把这个差值推到数百。
STATEMENT_DELTA_BUDGET = 20


class _StatementCounter:
    """统计这段区间里同步引擎实际执行的 SQL 语句（含 executemany 的每一条）。"""

    def __enter__(self):
        self.statements: list[str] = []
        self._listener = (
            lambda conn, cursor, statement, parameters, context, executemany: (
                self.statements.append(statement)
            )
        )
        event.listen(engine, "before_cursor_execute", self._listener)
        return self

    def __exit__(self, *exc_info):
        event.remove(engine, "before_cursor_execute", self._listener)
        return False


def _seed_scale(
    *,
    hosts: int = HOSTS,
    jobs_per_host: int = JOBS_PER_HOST,
    running_per_host: int = RUNNING_PER_HOST,
) -> dict:
    """30 host × 17 job（每 host 6 RUNNING + 11 PENDING）的 RUNNING run。

    用 Core 批量插入：造数本身不该成为用例耗时的大头（否则下一个人会为了跑得快
    把规模降回去，判据也就跟着失效了）。RUNNING 与 PENDING 混合是刻意的——#327
    是「以 RUNNING 为主」的第二波压力，只造 PENDING 会漏掉控制扇出那一半。
    """
    suffix = uuid4().hex[:8]
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        host_ids = [f"sc-{suffix}-{i:02d}" for i in range(hosts)]
        db.execute(
            insert(Host),
            [
                {
                    "id": hid,
                    "hostname": f"h-{hid}",
                    "status": HostStatus.ONLINE.value,
                    "created_at": now,
                }
                for hid in host_ids
            ],
        )
        plan_id = db.execute(
            insert(Plan)
            .values(
                name=f"scale-{suffix}",
                description="abort scale regression",
                created_by="pytest",
            )
            .returning(Plan.id)
        ).scalar_one()
        run_id = db.execute(
            insert(PlanRun)
            .values(
                plan_id=plan_id,
                status=PlanRunStatus.RUNNING.value,
                plan_snapshot={"name": f"scale-{suffix}", "plan_id": plan_id},
                run_type="MANUAL",
                triggered_by="pytest",
                started_at=now,
                total_job_count=hosts * jobs_per_host,
            )
            .returning(PlanRun.id)
        ).scalar_one()

        device_rows, layout = [], []
        for host_index, hid in enumerate(host_ids):
            for job_index in range(jobs_per_host):
                status = (
                    JobStatus.RUNNING if job_index < running_per_host else JobStatus.PENDING
                )
                device_rows.append(
                    {
                        "serial": f"SC{suffix.upper()}{host_index:02d}{job_index:02d}",
                        "host_id": hid,
                        "status": "ONLINE",
                        "tags": [],
                        "created_at": now,
                        "adb_connected": True,
                        "adb_state": "device",
                    }
                )
                layout.append((hid, status))
        device_ids = list(
            db.execute(insert(Device).returning(Device.id), device_rows).scalars()
        )
        job_rows = [
            {
                "plan_run_id": run_id,
                "plan_id": plan_id,
                "device_id": device_id,
                "host_id": hid,
                "status": status.value,
                "pipeline_def": PIPELINE_DEF,
                "created_at": now,
                "updated_at": now,
                "started_at": now if status is JobStatus.RUNNING else None,
            }
            for device_id, (hid, status) in zip(device_ids, layout, strict=True)
        ]
        job_ids = list(
            db.execute(insert(JobInstance).returning(JobInstance.id), job_rows).scalars()
        )
        db.execute(
            insert(PlanRunHost),
            [
                {
                    "plan_run_id": run_id,
                    "host_id": hid,
                    "device_count": jobs_per_host,
                    "status": "ADMITTED",
                    "coordinator_epoch": 1,
                    "admitted_at": now,
                }
                for hid in host_ids
            ],
        )
        db.commit()
        return {
            "host_ids": host_ids,
            "plan_id": plan_id,
            "plan_run_id": run_id,
            "device_ids": device_ids,
            "job_ids": job_ids,
            "total_jobs": len(job_ids),
            "running_jobs": hosts * running_per_host,
        }
    finally:
        db.close()


def _cleanup(seed: dict) -> None:
    db = SessionLocal()
    try:
        db.execute(text("SET statement_timeout = '30s'"))
        for job_id in seed["job_ids"]:
            for model, column in (
                (DeviceLease, DeviceLease.job_id),
                (StepTrace, StepTrace.job_id),
                (JobArtifact, JobArtifact.job_id),
            ):
                db.query(model).filter(column == job_id).delete()
        db.query(JobInstance).filter(JobInstance.id.in_(seed["job_ids"])).delete()
        db.query(PlanRunHost).filter(PlanRunHost.plan_run_id == seed["plan_run_id"]).delete()
        db.query(PlanRun).filter(PlanRun.id == seed["plan_run_id"]).delete()
        db.query(Plan).filter(Plan.id == seed["plan_id"]).delete()
        db.query(Device).filter(Device.id.in_(seed["device_ids"])).delete()
        db.query(Host).filter(Host.id.in_(seed["host_ids"])).delete()
        db.commit()
    finally:
        db.close()


def _abort_scale(seed: dict, **kwargs) -> dict:
    """跑真实 abort，只替掉与本次判据无关的外呼（socketio 投递 / 终态通知 / 锁时长）。"""
    fanout_calls: list[tuple[str, int]] = []
    control_calls: list[list] = []
    emit_events: list[str] = []
    with (
        patch("backend.services.plan_run_abort.notify_plan_run_terminal", lambda *a, **k: None),
        patch("backend.services.plan_run_abort.record_plan_run_abort_lock_seconds", lambda *a, **k: None),
        patch(
            "backend.services.plan_run_abort.record_plan_run_abort_fanout",
            lambda scope, jobs: fanout_calls.append((scope, int(jobs))),
        ),
        patch(
            "backend.services.plan_run_abort.schedule_agent_control_fanout",
            lambda items: control_calls.append(list(items)),
        ),
        patch(
            "backend.services.plan_run_abort.schedule_emit",
            lambda *args, **kwargs: emit_events.append(args[0]),
        ),
    ):
        db = SessionLocal()
        try:
            result = abort_plan_run(seed["plan_run_id"], db=db, reason="pytest-scale", **kwargs)
            db.commit()
        finally:
            db.close()
    return {
        "result": result,
        "fanout": fanout_calls,
        "control": control_calls,
        "emit_events": emit_events,
    }


def test_sql_statement_count_does_not_scale_with_job_count():
    """判据 1：30 host 不变、job 数 ×8.5，语句数只允许常数级增长。

    这是「abort 不需要与 job 数成正比的 DB 往返」的可测形式——#492/#988 的批量
    终态化正是为此；退回逐 job UPDATE/审计时这里会差出数百条语句。

    小样本刻意**同时含 PENDING 与 RUNNING**（每 host 各 1）：PENDING 的按 host
    聚合本身就与 host 数成正比（那是既定的 O(hosts) 设计），若小样本全 RUNNING，
    两轮走的路径不同，量到的就不是「job 数的影响」。当前实现实测 48 : 48（差 0）。
    """
    small = _seed_scale(jobs_per_host=2, running_per_host=1)
    big = _seed_scale()
    try:
        assert big["total_jobs"] == HOSTS * JOBS_PER_HOST == 510

        with _StatementCounter() as small_counter:
            _abort_scale(small)
        with _StatementCounter() as big_counter:
            _abort_scale(big)

        delta = len(big_counter.statements) - len(small_counter.statements)
        assert delta <= STATEMENT_DELTA_BUDGET, (
            f"30 host 不变、job 从 30 涨到 510 时语句数多出 {delta} 条"
            f"（预算 {STATEMENT_DELTA_BUDGET}）——DB 往返在随 job 数线性增长：\n"
            + "\n".join(big_counter.statements[:20])
        )
    finally:
        _cleanup(big)
        _cleanup(small)


def test_scale_abort_fans_out_once_and_summarizes_progress():
    """判据 2/3/4：扇出合并成一次、进度汇总成一条、样本如实标 run/510。"""
    seed = _seed_scale()
    try:
        out = _abort_scale(seed)

        assert out["fanout"] == [("run", seed["total_jobs"])], (
            "扇出样本必须恰好一条、scope=run、数量=本次牵动的 job 数（终态化 + 已下发"
            f"控制信号）：{out['fanout']}（总数 {seed['total_jobs']}）"
        )
        assert len(out["control"]) == 1, (
            f"控制扇出必须合并成一次（#703 合并前的形态是每 host 一次投递）："
            f"{len(out['control'])} 次"
        )
        assert len(out["control"][0]) == len(seed["host_ids"]), (
            f"control items 应按 host 覆盖全部 {len(seed['host_ids'])} 台："
            f"{len(out['control'][0])} 项"
        )
        assert out["emit_events"].count("job_status") == 1, (
            "大批量 abort 只发一条汇总 JOB_STATUS（#327：逐 job 推送打满事件循环）："
            f"{Counter(out['emit_events'])}"
        )
    finally:
        _cleanup(seed)


def test_scale_abort_terminalizes_pending_and_leaves_running_for_reaper():
    """终态语义不变式：PENDING 全部 →ABORTED；RUNNING 保持 RUNNING，等 Agent ack
    后由 reaper 收口（abort 本身不等 agent）。"""
    seed = _seed_scale()
    try:
        out = _abort_scale(seed)
        result = out["result"]
        assert (
            len(result.aborted_jobs) + len(result.abort_requested_jobs)
            == seed["total_jobs"]
        ), f"一次 abort 必须覆盖整轮 {seed['total_jobs']} 个 job：{result}"

        db = SessionLocal()
        try:
            statuses = Counter(
                db.execute(
                    select(JobInstance.status).where(JobInstance.id.in_(seed["job_ids"]))
                ).scalars()
            )
        finally:
            db.close()

        assert statuses[JobStatus.PENDING.value] == 0, f"仍有 PENDING 未终态化：{statuses}"
        assert statuses[JobStatus.ABORTED.value] == seed["total_jobs"] - seed["running_jobs"]
        assert statuses[JobStatus.RUNNING.value] == seed["running_jobs"], (
            f"RUNNING 必须在 abort 后保持 RUNNING（等 agent ack + reaper），实测 {statuses}"
        )
    finally:
        _cleanup(seed)
