"""#3723 v1.5 §3.3.2 A/E：陈旧候选预取后、job 锁前出现有效解除保护时不得写入 UNKNOWN。

规划核验确定性时序：recycler 预取无 abort 的 context → 另一事务提交
EXECUTING_DISARM + 新鲜进度 + 有效 abort → 最终写入口必须用最新已提交
run_context 复验并放弃写入。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os

import pytest

from backend.core.database import SessionLocal
from backend.core.job_timeout_config import disarm_grace_protects
from backend.models.audit import AuditLog
from backend.models.device_lease import DeviceLease
from backend.models.job import JobInstance, StepTrace
from backend.models.plan_run import PlanRun
from backend.scheduler import recycler
from backend.tests.scheduler.test_recycler import _cleanup_seed, _seed_running_job

PIPE = {
    "lifecycle": {
        "teardown": [
            {
                "step_id": "finish",
                "action": "script:powercycle_finish",
                "timeout_seconds": 600,
            }
        ]
    }
}


@pytest.mark.parametrize("path", ["running", "trace"])
def test_abort_arrives_between_context_prefetch_and_job_lock(
    db_session, monkeypatch, path,
):
    def forbidden(*args, **kwargs):
        raise AssertionError("Host signals forbidden")

    monkeypatch.setattr(os, "kill", forbidden)
    monkeypatch.setattr(os, "killpg", forbidden)

    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=2)
    seed = _seed_running_job(
        started_at=old,
        updated_at=now,
        pipeline_def=PIPE,
        execution_state="EXECUTING_STEP",
        last_execution_heartbeat_at=(old if path == "running" else now),
    )
    with SessionLocal() as db:
        db.add(
            DeviceLease(
                device_id=seed["device_id"],
                job_id=seed["job_id"],
                host_id=seed["host_id"],
                lease_type="JOB",
                status="ACTIVE",
                fencing_token="planner-race-token",
                lease_generation=1,
                agent_instance_id="planner-isolated",
                expires_at=now + timedelta(hours=1),
            )
        )
        db.commit()

    if path == "trace":
        monkeypatch.setattr(recycler, "STEP_TRACE_STALL_SECONDS", 300)
        with SessionLocal() as db:
            db.add(
                StepTrace(
                    job_id=seed["job_id"],
                    stage="init",
                    step_id="setup",
                    status="COMPLETED",
                    event_type="COMPLETED",
                    exit_code=0,
                    original_ts=old,
                )
            )
            db.commit()

    original = recycler._plan_run_contexts
    injected = False

    def contexts_then_abort(db, jobs):
        nonlocal injected
        snapshot = original(db, jobs)
        if not injected and any(j.id == seed["job_id"] for j in jobs):
            injected = True
            with SessionLocal() as other:
                job = other.get(JobInstance, seed["job_id"])
                job.execution_state = "EXECUTING_DISARM"
                job.last_progress_at = now
                run = other.get(PlanRun, seed["plan_run_id"])
                run.run_context = {
                    "abort_requested": {
                        "at": (now - timedelta(seconds=90)).isoformat(),
                    }
                }
                other.commit()
                assert disarm_grace_protects(job, run.run_context, now)
        return snapshot

    monkeypatch.setattr(recycler, "_plan_run_contexts", contexts_then_abort)
    monkeypatch.setattr(recycler, "_fill_deferred_post_completions", lambda *_: 0)
    monkeypatch.setattr(recycler, "_prune_steptrace_artifacts", lambda *_: None)
    monkeypatch.setattr(recycler, "schedule_emit", lambda *a, **k: None)

    try:
        recycler.recycle_once()
        assert injected, "竞态注入未触发：_plan_run_contexts 未被候选路径调用"
        with SessionLocal() as db:
            job = db.get(JobInstance, seed["job_id"])
            assert job.status == "RUNNING", {
                "status": job.status,
                "state": job.execution_state,
                "reason": job.status_reason,
            }
            assert job.execution_state == "EXECUTING_DISARM"
            lease = (
                db.query(DeviceLease)
                .filter(
                    DeviceLease.job_id == seed["job_id"],
                    DeviceLease.lease_type == "JOB",
                )
                .one()
            )
            assert lease.status == "ACTIVE"
            bad_actions = (
                ("job_running_timeout",)
                if path == "running"
                else ("step_trace_stall_detected",)
            )
            audits = (
                db.query(AuditLog)
                .filter(
                    AuditLog.resource_type == "job_instance",
                    AuditLog.resource_id == str(seed["job_id"]),
                    AuditLog.action.in_(bad_actions),
                )
                .all()
            )
            assert audits == [], [a.action for a in audits]
    finally:
        _cleanup_seed(seed)
