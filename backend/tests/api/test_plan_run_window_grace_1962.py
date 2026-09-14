"""#1962 — 终态 PlanRun 的仪表盘窗口必须容纳「延后落库」的本轮事件。

实测（live 数据，2026-09-14）：run 393 的窗口是 16:52:16→16:52:35，而它的
UNIVIEW 事件 **16:53:45** 才落库（晚 70 秒 —— reconciler 首个 tick 的
``ls`` + ``pull`` 慢于 job 本身的生命周期）。原窗口 ``[started_at, ended_at]``
把这条**本轮自己的**事件丢掉，于是「DLE 有行、异常仪表盘 0」。

本用例锁住两点：宽限内的迟到事件**计入**；超出宽限的**不计入**（防止把窗口
放宽成无界，从而把别轮事件也算进来）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.models.enums import JobStatus, PlanRunStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance, JobLogSignal
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uniview_extra(nfs_path: str, package: str) -> dict:
    return {
        "event_type": "UNIVIEW", "event_subtype": "Java Crash",
        "package_name": package, "entry_origin": "runtime",
        "nfs_path": nfs_path, "schema_version": 2,
    }


@pytest.fixture
def late_event_setup(db_session):
    host = Host(
        id="host-late", hostname="host-late", status="ONLINE", ip="10.9.1.1",
        ssh_user="root", ssh_port=22, extra={}, last_heartbeat=_now(),
    )
    db_session.add(host)
    dev = Device(
        serial="62002360", host_id="host-late", status="ONLINE",
        adb_connected=True, adb_state="device", platform="UNISOC",
    )
    db_session.add(dev)
    plan = Plan(name="#1962 窗口宽限", failure_threshold=0.05)
    db_session.add(plan)
    db_session.commit()

    ended = _now() - timedelta(minutes=50)
    run = PlanRun(
        plan_id=plan.id, status=PlanRunStatus.SUCCESS.value,
        failure_threshold=0.05,
        plan_snapshot={"plan": {"id": plan.id, "name": plan.name}, "steps": []},
        run_type="MANUAL", triggered_by="tester",
        started_at=ended - timedelta(seconds=20),   # 短 run：20 秒
        ended_at=ended,
    )
    db_session.add(run)
    db_session.commit()

    job = JobInstance(
        plan_run_id=run.id, plan_id=plan.id, device_id=dev.id, host_id=host.id,
        status=JobStatus.COMPLETED.value, pipeline_def={"lifecycle": {}},
        started_at=run.started_at, ended_at=ended,
    )
    db_session.add(job)
    db_session.commit()

    db_session.add_all([
        # 宽限内：run 结束 70 秒后落库（run 393 的真实形态）→ 必须计入
        JobLogSignal(
            id=21001, job_id=job.id, host_id=host.id, device_serial=dev.serial,
            seq_no=1, category="UNIVIEW", source="reconciler",
            path_on_device="JE.103000004",
            detected_at=ended + timedelta(seconds=70),
            extra=_uniview_extra("/nfs/late/JE.103000004", "com.android.camera2"),
        ),
        # 超出宽限：run 结束 45 分钟后落库（> LATE_EVENT_GRACE 30min）→ 不得计入
        JobLogSignal(
            id=21002, job_id=job.id, host_id=host.id, device_serial=dev.serial,
            seq_no=2, category="UNIVIEW", source="reconciler",
            path_on_device="JE.999999999",
            detected_at=ended + timedelta(minutes=45),
            extra=_uniview_extra("/nfs/very-late/JE.999999999", "com.too.late"),
        ),
    ])
    db_session.commit()
    return {"run": run, "ended_at": ended}


def test_late_event_within_grace_is_counted(client, auth_headers, late_event_setup):
    run = late_event_setup["run"]
    resp = client.get(
        f"/api/v1/plan-runs/{run.id}/watcher-summary",
        params={"time_scope": "all"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    section = data["current_run"]

    # 只有宽限内那条被计入
    assert section["total_events"] == 1, section
    packages = [row["package_name"] for row in section["package_ranking"]]
    assert "com.android.camera2" in packages, packages
    assert "com.too.late" not in packages, packages

    # 报出的窗口末端包含宽限（所见即所计）
    window_end = datetime.fromisoformat(data["window_end_at"])
    assert window_end > late_event_setup["ended_at"], (window_end, late_event_setup["ended_at"])


def test_relative_scope_stays_strict(client, auth_headers, late_event_setup):
    """防线：相对范围（1h/15m…）是「run 内的相对切片」，不因宽限而变大。

    否则「15m」会静默变成 45m（标签说谎）。这些范围本就被 clamp 在 run 内
    （``cur_start = max(run_start, window_end - N)``），因此迟到事件不在其中。
    """
    run = late_event_setup["run"]
    resp = client.get(
        f"/api/v1/plan-runs/{run.id}/watcher-summary",
        params={"time_scope": "1h"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    window_end = datetime.fromisoformat(data["window_end_at"])
    assert window_end == late_event_setup["ended_at"], (window_end, late_event_setup["ended_at"])
    # 两条信号都晚于 run 结束（+70s 与 +45min），故都不在相对切片内
    assert data["current_run"]["total_events"] == 0, data["current_run"]


def test_running_run_window_not_extended(client, auth_headers, db_session):
    """防线：RUNNING run（无 ended_at）不适用宽限，窗口末端仍是 now。"""
    host = Host(
        id="host-run", hostname="host-run", status="ONLINE", ip="10.9.1.2",
        ssh_user="root", ssh_port=22, extra={}, last_heartbeat=_now(),
    )
    db_session.add(host)
    plan = Plan(name="#1962 RUNNING", failure_threshold=0.05)
    db_session.add(plan)
    db_session.commit()
    run = PlanRun(
        plan_id=plan.id, status=PlanRunStatus.RUNNING.value,
        failure_threshold=0.05,
        plan_snapshot={"plan": {"id": plan.id, "name": plan.name}, "steps": []},
        run_type="MANUAL", triggered_by="tester",
        started_at=_now() - timedelta(minutes=5),
    )
    db_session.add(run)
    db_session.commit()

    resp = client.get(
        f"/api/v1/plan-runs/{run.id}/watcher-summary",
        params={"time_scope": "all"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    window_end = datetime.fromisoformat(data["window_end_at"])
    assert window_end <= _now() + timedelta(seconds=5), window_end
