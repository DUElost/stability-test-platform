"""#703 第 3 面：一次 abort 的**扇出规模**必须落成可查序列，且按作用域分开。

埋点本身不是判据——判据是「作用域错位能不能在数据上被看见」。#1880 的形态是
`POST /hosts/{id}/hot-update?abort_running_jobs=true`（单台 host 升级）实际终态化了
**整轮 run**：当时日志里只能逐条数、指标上零痕迹，所以这类错位只能靠人复述。
本文件钉两条：

1. `scope` 取的是**实际作用域**（`host_id` 有/无），不是审计文案；
2. host 级 abort 的样本数量只含**该 host 的 job**——另一台 host 的 job 既不进样本，
   也不改状态（否则 `scope="host"` 这条序列就会与 `run` 同形，等于没埋）。

需要 PostgreSQL（conftest 的 testcontainers / CI 的 PG service）。按仓库口径**刻意不写**
「非 PG 就 skip」的分支：那会让环境问题静默变成一条假绿。
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import text

from backend.core.database import SessionLocal
from backend.core.metrics import record_plan_run_abort_fanout
from backend.models.device_lease import DeviceLease
from backend.models.enums import HostStatus, JobStatus, PlanRunStatus
from backend.models.host import Device, Host
from backend.models.job import JobArtifact, JobInstance, StepTrace
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun, PlanRunHost
from backend.services.plan_run_abort import abort_plan_run

PIPELINE_DEF = {"lifecycle": {"init": [], "teardown": []}}


def _seed_two_hosts() -> dict:
    """一个 RUNNING run，job 分布在两台 host：A = 2 PENDING + 1 RUNNING，B = 2 PENDING。

    3 / 2 这两个数不同，扇出数才会暴露「越界」（读到 5 就说明 host 作用域失效）。
    `(plan_run_id, device_id)` 有唯一约束（一 run 一设备一 job），所以每个 job 一台设备。
    """
    suffix = uuid4().hex[:8]
    host_a = f"af-a-{suffix}"
    host_b = f"af-b-{suffix}"
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        hosts = [
            Host(id=hid, hostname=f"h-{hid}", status=HostStatus.ONLINE.value, created_at=now)
            for hid in (host_a, host_b)
        ]
        plan = Plan(
            name=f"fanout-{suffix}", description="abort fanout metric",
            created_by="pytest",
        )
        db.add_all([*hosts, plan])
        db.flush()

        # host A 三台设备（含 1 个 RUNNING），host B 两台（全 PENDING）
        layout = [
            (host_a, JobStatus.PENDING), (host_a, JobStatus.PENDING), (host_a, JobStatus.RUNNING),
            (host_b, JobStatus.PENDING), (host_b, JobStatus.PENDING),
        ]
        devices, jobs = [], []
        for idx, (hid, status) in enumerate(layout):
            device = Device(
                serial=f"AF{suffix.upper()}{idx}", host_id=hid, status="ONLINE", tags=[],
                created_at=now, adb_connected=True, adb_state="device",
            )
            db.add(device)
            db.flush()
            devices.append(device)
            jobs.append(JobInstance(
                plan_run_id=0, plan_id=plan.id, device_id=device.id, host_id=hid,
                status=status.value, pipeline_def=PIPELINE_DEF,
                created_at=now, updated_at=now,
                started_at=now if status is JobStatus.RUNNING else None,
            ))
        run = PlanRun(
            plan_id=plan.id, status=PlanRunStatus.RUNNING.value, plan_snapshot={"name": plan.name, "plan_id": plan.id}, run_type="MANUAL",
            triggered_by="pytest", started_at=now, total_job_count=len(layout),
        )
        db.add(run)
        db.flush()
        for job in jobs:
            job.plan_run_id = run.id
        db.add_all(jobs)
        db.flush()
        prhs = [
            PlanRunHost(
                plan_run_id=run.id, host_id=hid, device_count=count, status="ADMITTED",
                coordinator_epoch=1, admitted_at=now,
            )
            for hid, count in ((host_a, 3), (host_b, 2))
        ]
        db.add_all(prhs)
        db.flush()
        seed = {
            "host_a": host_a, "host_b": host_b, "plan_id": plan.id, "plan_run_id": run.id,
            "device_ids": [d.id for d in devices], "job_ids": [j.id for j in jobs],
            "prh_ids": [p.id for p in prhs],
        }
        db.commit()
        return seed
    finally:
        db.close()


def _cleanup(seed: dict) -> None:
    db = SessionLocal()
    try:
        db.execute(text("SET statement_timeout = '15s'"))
        for job_id in seed["job_ids"]:
            for model, column in (
                (DeviceLease, DeviceLease.job_id),
                (StepTrace, StepTrace.job_id),
                (JobArtifact, JobArtifact.job_id),
            ):
                db.query(model).filter(column == job_id).delete()
        db.query(JobInstance).filter(JobInstance.id.in_(seed["job_ids"])).delete()
        db.query(PlanRunHost).filter(PlanRunHost.id.in_(seed["prh_ids"])).delete()
        db.query(PlanRun).filter(PlanRun.id == seed["plan_run_id"]).delete()
        db.query(PlanStep).filter(PlanStep.plan_id == seed["plan_id"]).delete()
        db.query(Plan).filter(Plan.id == seed["plan_id"]).delete()
        db.query(Device).filter(Device.id.in_(seed["device_ids"])).delete()
        db.query(Host).filter(Host.id.in_([seed["host_a"], seed["host_b"]])).delete()
        db.commit()
    finally:
        db.close()


def _capture_fanout():
    """收集 `record_plan_run_abort_fanout(scope, jobs)` 的调用。"""
    calls: list[tuple[str, int]] = []
    return calls, lambda scope, jobs: calls.append((scope, int(jobs)))


def _abort(seed: dict, **kwargs):
    """跑一次 abort，只替掉与观测无关的外呼（emit / notify / 锁时长指标）。"""
    calls, recorder = _capture_fanout()
    db = SessionLocal()
    try:
        with (
            patch("backend.services.plan_run_abort.notify_plan_run_terminal", lambda *a, **k: None),
            patch("backend.services.plan_run_finalization.notify_plan_run_terminal", lambda *a, **k: None),
            patch("backend.services.plan_run_abort.record_plan_run_abort_lock_seconds", lambda *a, **k: None),
            patch("backend.services.plan_run_abort.record_plan_run_abort_fanout", recorder),
        ):
            result = abort_plan_run(seed["plan_run_id"], db=db, reason="pytest-fanout", **kwargs)
        db.commit()
    finally:
        db.close()
    return calls, result


def test_run_scope_abort_observes_every_touched_job_once():
    """run 级：A(2 PENDING + 1 RUNNING) + B(2 PENDING) 共 5 个 job，一次样本、scope=run。"""
    seed = _seed_two_hosts()
    try:
        calls, result = _abort(seed, triggered_by="pytest")
        assert [c[0] for c in calls] == ["run"], f"必须且只记一次，scope=run：{calls}"
        assert calls[0][1] == 5, (
            f"扇出样本应等于本次实际牵动的 job 数（终态化 + 已下发控制信号）：{calls} / {result}"
        )
    finally:
        _cleanup(seed)


def test_host_scope_abort_counts_only_that_hosts_jobs():
    """host 级：只报该 host 的 3 个 job；另一台 host 不得被卷进样本或状态。"""
    seed = _seed_two_hosts()
    try:
        calls, _result = _abort(
            seed, triggered_by="pytest", host_id=seed["host_a"],
        )
        assert [c[0] for c in calls] == ["host"], (
            f"host 级 abort 必须标成 host——与 run 混在一起这条序列就没有判据价值：{calls}"
        )
        assert calls[0][1] == 3, (
            f"越界了：host 作用域读到 {calls[0][1]}（应为该 host 的 3 个 job，"
            "整轮是 5）——正是 #1880 的形态"
        )
        db = SessionLocal()
        try:
            other = (
                db.query(JobInstance)
                .filter(JobInstance.host_id == seed["host_b"])
                .all()
            )
            assert [j.status for j in other] == [JobStatus.PENDING.value] * 2, (
                "另一台 host 的 job 被 host 级 abort 改动了状态"
            )
        finally:
            db.close()
    finally:
        _cleanup(seed)


class TestFanoutLabelDiscipline:
    """helper 自身的值域纪律（#1927：label 值必须是有界集合，否则指标自己变泄漏面）。"""

    @pytest.mark.parametrize(
        "scope,expected",
        [("run", "run"), ("host", "host"), ("weird-scope", "unknown"), (None, "unknown")],
    )
    def test_unknown_scope_collapses_to_unknown(self, scope, expected):
        from backend.core import metrics

        if not metrics.PROMETHEUS_AVAILABLE:
            pytest.skip("prometheus_client 不可用")
        from prometheus_client import REGISTRY

        def sample() -> float:
            return REGISTRY.get_sample_value(
                "stability_plan_run_abort_fanout_jobs_count", {"scope": expected}
            ) or 0.0

        before = sample()
        record_plan_run_abort_fanout(scope, 3)
        assert sample() == before + 1

    def test_negative_jobs_is_not_observed(self):
        from backend.core import metrics

        if not metrics.PROMETHEUS_AVAILABLE:
            pytest.skip("prometheus_client 不可用")
        from prometheus_client import REGISTRY

        before = REGISTRY.get_sample_value(
            "stability_plan_run_abort_fanout_jobs_count", {"scope": "run"}
        ) or 0.0
        record_plan_run_abort_fanout("run", -1)
        record_plan_run_abort_fanout("run", "not-a-number")
        assert REGISTRY.get_sample_value(
            "stability_plan_run_abort_fanout_jobs_count", {"scope": "run"}
        ) == before


class TestObservabilityCannotBreakAbort:
    """#703 残留②：观测回写炸了，**已提交的** abort 也不得跟着变 500。

    abort 族的 5 个调用点（``record_plan_run_abort_lock_seconds``×4 +
    ``record_plan_run_abort_fanout``×1）全部位于 ``db.commit()`` 之后的返回路径；
    判据直接向 Histogram 对象注入异常（模拟 prometheus_client 内部失败），要求
    record 函数吞下。**反向验证**：去掉 ``metrics._safe_emit`` 包裹，本类两条
    用例当场红。
    """

    class _Boom:
        def labels(self, **_kw):
            raise RuntimeError("prometheus internals exploded")

    def test_broken_fanout_histogram_does_not_raise(self, monkeypatch):
        from backend.core import metrics

        if not metrics.PROMETHEUS_AVAILABLE:
            pytest.skip("prometheus_client 不可用")
        monkeypatch.setattr(metrics, "plan_run_abort_fanout_jobs", self._Boom())
        record_plan_run_abort_fanout("run", 2)  # 不得抛

    def test_broken_lock_seconds_histogram_does_not_raise(self, monkeypatch):
        from backend.core import metrics

        if not metrics.PROMETHEUS_AVAILABLE:
            pytest.skip("prometheus_client 不可用")
        monkeypatch.setattr(metrics, "plan_run_abort_lock_seconds", self._Boom())
        metrics.record_plan_run_abort_lock_seconds(0.5, "finalize")  # 不得抛
