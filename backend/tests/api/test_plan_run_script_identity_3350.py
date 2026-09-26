"""#3350（ADR-0023 D2）：观测端点脚本身份派生——从 `plan_snapshot.steps` 查表。

判据逐条对应 ADR-0023「2026-09-25 裁决」C2 行（pytest 6 cases）：
① events 端点 step 类事件含脚本身份；② log_signal/audit/trigger 类事件字段为 None；
③ devices 端点 current_step 命中 snapshot → 含脚本身份；④ 不在 snapshot（边界）→ None；
⑤ timeline 端点 StageStepOut 含 version；⑥ 快照缺失（早期遗留 PlanRun）→ 字段全 None、不抛错。

为什么是「查快照」而不是「查当前 PlanStep」：排查要回答的是**当时跑的是哪个版本**，
现行 PlanStep 可能已被改指别的版本——那样会给出看起来合理的错答案。
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.models.audit import AuditLog
from backend.models.job import JobInstance, JobLogSignal, StepTrace
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun

SNAPSHOT_STEPS = [
    {
        "stage": "init", "step_key": "prepare", "script_name": "device_prepare",
        "script_version": "1.2.3", "sort_order": 1,
    },
    {
        "stage": "patrol", "step_key": "monkey_check", "script_name": "monkey_test",
        "script_version": "5.2.0", "sort_order": 1,
    },
]


def _seed(db_session, sample_device, *, plan_snapshot, current_patrol_step="monkey_check"):
    now = datetime.now(timezone.utc)
    plan = Plan(name="script-identity-3350", description="")
    db_session.add(plan)
    db_session.flush()

    run = PlanRun(
        plan_id=plan.id, status="RUNNING", plan_snapshot=plan_snapshot,
        run_type="MANUAL", triggered_by="pytest", started_at=now,
    )
    db_session.add(run)
    db_session.flush()

    job = JobInstance(
        plan_run_id=run.id, plan_id=plan.id,
        device_id=sample_device.id, host_id=sample_device.host_id,
        status="RUNNING", current_patrol_step=current_patrol_step,
        patrol_cycle_count=1, last_patrol_heartbeat_at=now,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        started_at=now,
    )
    db_session.add(job)
    db_session.flush()

    # step 级失败（category=step，应带身份）
    db_session.add(StepTrace(
        job_id=job.id, step_id="monkey_check", stage="patrol",
        status="FAILED", event_type="FAILED", original_ts=now,
        error_message="monkey crashed",
    ))
    # job 级失败（step_id=__job__，不在快照 → None）
    db_session.add(StepTrace(
        job_id=job.id, step_id="__job__", stage="system",
        status="FAILED", event_type="RUN_COMPLETE", original_ts=now,
    ))
    # log_signal 事件（category=log_signal，应恒 None）
    db_session.add(JobLogSignal(
        job_id=job.id, host_id=sample_device.host_id,
        device_serial=sample_device.serial, seq_no=1, category="ANR",
        source="logcat", path_on_device="/data/anr/traces.txt",
        first_lines="ANR in com.example", detected_at=now,
    ))
    # audit 事件（category=audit，应恒 None）
    db_session.add(AuditLog(
        action="abort_plan_run", resource_type="plan_run",
        resource_id=str(run.id), details={"reason": "pytest"}, timestamp=now,
    ))
    db_session.commit()
    return run, job


def test_events_step_event_carries_script_identity(client, auth_headers, db_session, sample_device):
    run, _job = _seed(db_session, sample_device, plan_snapshot={"steps": SNAPSHOT_STEPS})

    resp = client.get(f"/api/v1/plan-runs/{run.id}/events", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    events = resp.json()["data"]["events"]

    step_events = [e for e in events if e["category"] == "step" and e["title"] == "patrol.monkey_check 失败"]
    assert len(step_events) == 1, [e["title"] for e in events]
    assert step_events[0]["script_name"] == "monkey_test"
    assert step_events[0]["script_version"] == "5.2.0"


def test_events_non_step_categories_have_no_script_identity(client, auth_headers, db_session, sample_device):
    run, _job = _seed(db_session, sample_device, plan_snapshot={"steps": SNAPSHOT_STEPS})

    events = client.get(f"/api/v1/plan-runs/{run.id}/events", headers=auth_headers).json()["data"]["events"]
    categories = {e["category"] for e in events}
    # 反空转：三类非 step 事件确实在场，Null 断言才有意义
    assert {"trigger", "audit", "log_signal"} <= categories, categories
    for event in events:
        if event["category"] == "step":
            continue
        assert event["script_name"] is None, event
        assert event["script_version"] is None, event
    # job 级 step 事件（__job__）同样查不到身份
    job_level = [e for e in events if e["category"] == "step" and "Job #" in e["title"]]
    assert job_level and all(e["script_name"] is None for e in job_level)


def test_devices_current_step_resolves_script_identity(client, auth_headers, db_session, sample_device):
    run, job = _seed(db_session, sample_device, plan_snapshot={"steps": SNAPSHOT_STEPS})

    data = client.get(f"/api/v1/plan-runs/{run.id}/devices", headers=auth_headers).json()["data"]
    item = next(d for d in data["devices"] if d["job_id"] == job.id)
    assert item["current_step"] == "monkey_check"
    assert item["current_script_name"] == "monkey_test"
    assert item["current_script_version"] == "5.2.0"


def test_devices_current_step_not_in_snapshot_is_none(client, auth_headers, db_session, sample_device):
    """边界④：心跳报的 step 不在快照里（改版后的残留心跳/手写值）→ 不猜，返回 None。"""
    run, job = _seed(
        db_session, sample_device,
        plan_snapshot={"steps": SNAPSHOT_STEPS},
        current_patrol_step="ghost_step",
    )

    data = client.get(f"/api/v1/plan-runs/{run.id}/devices", headers=auth_headers).json()["data"]
    item = next(d for d in data["devices"] if d["job_id"] == job.id)
    assert item["current_script_name"] is None
    assert item["current_script_version"] is None


def test_timeline_stage_steps_carry_version(client, auth_headers, db_session, sample_device):
    run, _job = _seed(db_session, sample_device, plan_snapshot={"steps": SNAPSHOT_STEPS})

    stages = client.get(f"/api/v1/plan-runs/{run.id}/timeline", headers=auth_headers).json()["data"]["stages"]
    by_key = {
        step["step_key"]: step
        for stage in stages for step in stage["steps"]
    }
    assert by_key["monkey_check"]["script_name"] == "monkey_test"
    assert by_key["monkey_check"]["script_version"] == "5.2.0"
    assert by_key["prepare"]["script_version"] == "1.2.3"


def test_legacy_snapshot_yields_none_everywhere(client, auth_headers, db_session, sample_device):
    """⑥ 早期 PlanRun（快照为空/缺 steps）→ 三端点字段全 None，不抛错。"""
    run, job = _seed(db_session, sample_device, plan_snapshot={})

    events = client.get(f"/api/v1/plan-runs/{run.id}/events", headers=auth_headers)
    assert events.status_code == 200, events.text
    assert all(e["script_name"] is None for e in events.json()["data"]["events"])

    devices = client.get(f"/api/v1/plan-runs/{run.id}/devices", headers=auth_headers)
    assert devices.status_code == 200, devices.text
    item = next(d for d in devices.json()["data"]["devices"] if d["job_id"] == job.id)
    assert item["current_script_name"] is None

    timeline = client.get(f"/api/v1/plan-runs/{run.id}/timeline", headers=auth_headers)
    assert timeline.status_code == 200, timeline.text
    for stage in timeline.json()["data"]["stages"]:
        for step in stage["steps"]:
            assert step["script_version"] is None
