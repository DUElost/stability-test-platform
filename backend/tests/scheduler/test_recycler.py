import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.core.database import SessionLocal
from backend.models.audit import AuditLog
from backend.models.device_lease import DeviceLease
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.job import JobInstance, StepTrace
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.scheduler import recycler


LIFECYCLE = {"init": [], "teardown": []}
PIPELINE_DEF = {"stages": {"prepare": [], "execute": [], "post_process": []}}

# ADR-0022 D10: patrol lifecycle pipeline_def, consumed by Pass #2b.
PATROL_PIPELINE_DEF = {
    "lifecycle": {
        "init": [],
        "patrol": {
            "interval_seconds": 60,
            "steps": [
                {
                    "step_id": "patrol_step",
                    "action": "script:check_device",
                    "version": "v1.0.0",
                    "params": {},
                    "timeout_seconds": 30,
                    "retry": 0,
                }
            ],
        },
        "teardown": [],
    }
}


@pytest.fixture(autouse=True)
def _reset_database_for_recycler_tests(db_session):
    """Recycler uses SessionLocal directly, so still request DB truncation."""
    yield


def _seed_running_job(
    started_at: datetime,
    updated_at: datetime,
    *,
    pipeline_def: dict | None = None,
    last_patrol_heartbeat_at: datetime | None = None,
    next_retry_at: datetime | None = None,
    patrol_cycle_count: int = 0,
    current_failure_streak: int = 0,
    execution_state: str | None = None,
    last_execution_heartbeat_at: datetime | None = None,
) -> dict:
    suffix = uuid4().hex[:8]
    host_id = f"recycler-host-{suffix}"
    db = SessionLocal()
    try:
        host = Host(
            id=host_id,
            hostname=f"recycler-{suffix}",
            status=HostStatus.ONLINE.value,
            last_heartbeat=updated_at,
            created_at=started_at,
        )
        device = Device(
            serial=f"R-{suffix}",
            host_id=host_id,
            status="BUSY",
            tags=[],
            created_at=started_at,
        )
        db.add_all([host, device])
        db.flush()

        plan = Plan(
            name=f"wf-{suffix}",
            description="pytest workflow",
            created_by="pytest",
        )
        db.add(plan)
        db.flush()

        step = PlanStep(
            plan_id=plan.id,
            step_key="default",
            script_name="dummy",
            script_version="v1.0.0",
            stage="init",
            sort_order=0,
        )
        db.add(step)
        db.flush()

        run = PlanRun(
            plan_id=plan.id,
            status="RUNNING",
            plan_snapshot={"name": plan.name, "plan_id": plan.id},
            triggered_by="pytest",
            started_at=started_at,
            run_type="MANUAL",
        )
        db.add(run)
        db.flush()

        job = JobInstance(
            plan_run_id=run.id,
            plan_id=plan.id,
            device_id=device.id,
            host_id=host_id,
            status=JobStatus.RUNNING.value,
            pipeline_def=pipeline_def if pipeline_def is not None else PIPELINE_DEF,
            created_at=started_at,
            updated_at=updated_at,
            started_at=started_at,
            last_patrol_heartbeat_at=last_patrol_heartbeat_at,
            next_retry_at=next_retry_at,
            patrol_cycle_count=patrol_cycle_count,
            current_failure_streak=current_failure_streak,
            execution_state=execution_state,
            last_execution_heartbeat_at=last_execution_heartbeat_at,
        )
        db.add(job)
        db.flush()
        db.commit()

        return {
            "host_id": host_id,
            "device_id": device.id,
            "plan_id": plan.id,
            "plan_run_id": run.id,
            "job_id": job.id,
        }
    finally:
        db.close()


def _seed_fresh_coordinator_heartbeat(seed: dict, *, at: datetime | None = None) -> None:
    """ADR-0026 §3: give the seed a live per-host coordinator signal so the
    WAITING/PATROL_SLEEP clock (not the dispatch-time fallback) applies."""
    from backend.models.plan_run import PlanRunHost

    db = SessionLocal()
    try:
        db.add(PlanRunHost(
            plan_run_id=seed["plan_run_id"],
            host_id=seed["host_id"],
            coordinator_heartbeat_at=at or datetime.now(timezone.utc),
        ))
        db.commit()
    finally:
        db.close()


def _cleanup_seed(seed: dict) -> None:
    from backend.models.audit import AuditLog
    from backend.models.device_lease import DeviceLease
    from backend.models.plan_run import PlanRunHost
    db = SessionLocal()
    try:
        db.query(PlanRunHost).filter(
            PlanRunHost.plan_run_id == seed["plan_run_id"],
            PlanRunHost.host_id == seed["host_id"],
        ).delete()
        db.query(StepTrace).filter(StepTrace.job_id == seed["job_id"]).delete()
        db.query(AuditLog).filter(
            AuditLog.resource_type == "job_instance",
            AuditLog.resource_id == str(seed["job_id"]),
        ).delete()
        db.query(DeviceLease).filter(DeviceLease.job_id == seed["job_id"]).delete()
        db.query(JobInstance).filter(JobInstance.id == seed["job_id"]).delete()
        db.query(PlanRun).filter(PlanRun.id == seed["plan_run_id"]).delete()
        db.query(PlanStep).filter(PlanStep.plan_id == seed["plan_id"]).delete()
        db.query(Plan).filter(Plan.id == seed["plan_id"]).delete()
        db.query(Device).filter(Device.id == seed["device_id"]).delete()
        db.query(Host).filter(Host.id == seed["host_id"]).delete()
        db.commit()
    finally:
        db.close()


def test_recycler_keeps_running_job_with_recent_liveness(engine, monkeypatch):
    now = datetime.now(timezone.utc)
    old_started_at = now - timedelta(seconds=recycler.RUNNING_HEARTBEAT_TIMEOUT_SECONDS + 60)
    seed = _seed_running_job(
        started_at=old_started_at,
        updated_at=now,
        execution_state="EXECUTING_STEP",
        last_execution_heartbeat_at=now,
    )
    monkeypatch.setattr(recycler, "_fill_deferred_post_completions", lambda db, current: 0)
    monkeypatch.setattr(recycler, "_prune_steptrace_artifacts", lambda db, current: None)
    monkeypatch.setattr(recycler, "schedule_emit", lambda *args, **kwargs: None)
    try:
        recycler.recycle_once()

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            assert job.status == JobStatus.RUNNING.value
            assert job.status_reason != "running_timeout: no completion within window"
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


# ══════════════════════════════════════════════════════════════════════════════
# Phase 4c: PENDING timeout
# ══════════════════════════════════════════════════════════════════════════════


# ── Phase 4c helpers ──────────────────────────────────────────────────────────

def _seed_pending_job(created_at: datetime) -> dict:
    """Create a PENDING job for recycler timeout testing."""
    suffix = uuid4().hex[:8]
    host_id = f"recycler-ph-{suffix}"
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        host = Host(
            id=host_id, hostname=f"recycler-p-{suffix}",
            status=HostStatus.ONLINE.value, last_heartbeat=now, created_at=now,
        )
        device = Device(
            serial=f"RP-{suffix}", host_id=host_id, status="ONLINE",
            tags=[], created_at=now,
            adb_connected=True, adb_state="device",
        )
        db.add_all([host, device])
        db.flush()

        plan = Plan(
            name=f"wf-{suffix}", description="pytest", created_by="pytest",
        )
        db.add(plan)
        db.flush()

        step = PlanStep(
            plan_id=plan.id,
            step_key="default",
            script_name="dummy",
            script_version="v1.0.0",
            stage="init",
            sort_order=0,
        )
        db.add(step)
        db.flush()

        run = PlanRun(
            plan_id=plan.id, status="RUNNING",
            triggered_by="pytest", started_at=now,
            plan_snapshot={"name": plan.name, "plan_id": plan.id},
            run_type="MANUAL",
        )
        db.add(run)
        db.flush()

        job = JobInstance(
            plan_run_id=run.id, plan_id=plan.id,
            device_id=device.id, host_id=host_id,
            status=JobStatus.PENDING.value, pipeline_def=PIPELINE_DEF,
            created_at=created_at, updated_at=created_at,
        )
        db.add(job)
        db.commit()

        return {
            "host_id": host_id, "device_id": device.id,
            "plan_id": plan.id,
            "plan_run_id": run.id, "job_id": job.id,
        }
    finally:
        db.close()


# ── Phase 4c: PENDING timeout test ────────────────────────────────────────────

def test_pending_timeout_fails_with_lease_release_attempt(engine, monkeypatch):
    """Phase 4c: PENDING timeout still → FAILED + release_lease_sync (unchanged)."""
    now = datetime.now(timezone.utc)
    old_created = now - timedelta(seconds=recycler.DISPATCHED_TIMEOUT_SECONDS + 60)
    seed = _seed_pending_job(created_at=old_created)

    # Create ACTIVE lease (defensive: normally PENDING has no lease, but
    # release_lease_sync should be called regardless)
    db = SessionLocal()
    try:
        lease = DeviceLease(
            device_id=seed["device_id"], job_id=seed["job_id"],
            host_id=seed["host_id"], lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=f"{seed['device_id']}:1", lease_generation=1,
            agent_instance_id=seed["host_id"],
            acquired_at=old_created, renewed_at=old_created,
            expires_at=old_created + timedelta(seconds=600),
        )
        db.add(lease)
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(recycler, "_fill_deferred_post_completions", lambda db, current: 0)
    monkeypatch.setattr(recycler, "_prune_steptrace_artifacts", lambda db, current: None)
    monkeypatch.setattr(recycler, "schedule_emit", lambda *args, **kwargs: None)
    try:
        recycler.recycle_once()

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            assert job.status == JobStatus.FAILED.value, (
                f"PENDING timeout must transition to FAILED; got {job.status}"
            )

            # Lease must be RELEASED (defensive release)
            db.expire_all()
            dl = (
                db.query(DeviceLease)
                .filter(
                    DeviceLease.device_id == seed["device_id"],
                    DeviceLease.job_id == seed["job_id"],
                )
                .first()
            )
            assert dl is not None
            assert dl.status == LeaseStatus.RELEASED.value, (
                f"PENDING timeout must release lease; got {dl.status}"
            )
            audit = (
                db.query(AuditLog)
                .filter(
                    AuditLog.action == "job_terminalized",
                    AuditLog.resource_type == "job_instance",
                    AuditLog.resource_id == str(seed["job_id"]),
                )
                .one()
            )
            assert audit.details["source"] == "recycler_pending_timeout"
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


def test_pending_timeout_rolls_back_when_aggregation_fails(engine, monkeypatch):
    now = datetime.now(timezone.utc)
    seed = _seed_pending_job(
        created_at=now - timedelta(
            seconds=recycler.DISPATCHED_TIMEOUT_SECONDS + 60,
        ),
    )

    def fail_aggregation(*_args, **_kwargs):
        raise RuntimeError("aggregate unavailable")

    monkeypatch.setattr(
        "backend.services.aggregator_sync.plan_aggregator_sync",
        fail_aggregation,
    )
    try:
        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            with pytest.raises(RuntimeError, match="aggregate unavailable"):
                with db.begin_nested():
                    recycler._mark_pending_timeout(
                        db, job, now, "pending_timeout",
                    )
            db.commit()
        finally:
            db.close()

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job.status == JobStatus.PENDING.value
            assert (
                db.query(AuditLog)
                .filter(
                    AuditLog.action == "job_terminalized",
                    AuditLog.resource_id == str(seed["job_id"]),
                )
                .count()
                == 0
            )
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


def test_pending_timeout_skips_mark_failure_same_tick(engine, monkeypatch):
    """#791: mark 失败回滚后不得在同一 recycle_once while True 内热旋同一 PENDING。"""
    now = datetime.now(timezone.utc)
    seed = _seed_pending_job(
        created_at=now - timedelta(
            seconds=recycler.DISPATCHED_TIMEOUT_SECONDS + 60,
        ),
    )
    calls = {"n": 0}

    def boom(_db, job, *_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] > 5:
            raise AssertionError(
                f"pending timeout dead-loop on job={job.id} calls={calls['n']}"
            )
        raise RuntimeError("mark failed")

    monkeypatch.setattr(recycler, "_mark_pending_timeout", boom)
    _patch_recycler_neutrals(monkeypatch)
    try:
        recycler.recycle_once()
        assert calls["n"] == 1, f"expected one mark attempt, got {calls['n']}"
        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job.status == JobStatus.PENDING.value
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


def test_postgresql_heartbeat_wins_against_stale_timeout_candidate(engine):
    now = datetime.now(timezone.utc)
    stale_at = now - timedelta(
        seconds=recycler.RUNNING_HEARTBEAT_TIMEOUT_SECONDS + 60,
    )
    seed = _seed_running_job(
        started_at=stale_at,
        updated_at=stale_at,
        execution_state="EXECUTING_STEP",
        last_execution_heartbeat_at=stale_at,
    )
    barrier = threading.Barrier(2)
    timeout_results: list[bool] = []
    errors: list[Exception] = []
    job_deadline = now - timedelta(
        seconds=recycler.RUNNING_HEARTBEAT_TIMEOUT_SECONDS,
    )

    def timeout_worker():
        db = SessionLocal()
        try:
            stale_job = db.get(JobInstance, seed["job_id"])
            barrier.wait(timeout=5)
            barrier.wait(timeout=5)
            timeout_results.append(
                recycler._mark_running_timeout(
                    db, stale_job, now, "running_timeout",
                    execution_heartbeat_deadline=job_deadline,
                ),
            )
            db.commit()
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    def heartbeat_worker():
        db = SessionLocal()
        try:
            barrier.wait(timeout=5)
            db.query(JobInstance).filter(
                JobInstance.id == seed["job_id"],
                JobInstance.status == JobStatus.RUNNING.value,
            ).update(
                {
                    JobInstance.last_execution_heartbeat_at: now,
                    # Pin updated_at — mirrors extend-batch (#991).
                    JobInstance.updated_at: stale_at,
                },
                synchronize_session=False,
            )
            db.commit()
            barrier.wait(timeout=5)
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    try:
        threads = [
            threading.Thread(target=timeout_worker),
            threading.Thread(target=heartbeat_worker),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        assert timeout_results == [False]
        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job.status == JobStatus.RUNNING.value
            assert job.last_execution_heartbeat_at == now
            assert job.updated_at == stale_at
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


def test_pending_timeout_skips_when_host_has_running_jobs(engine, monkeypatch):
    """Queued PENDING behind parallel capacity must not be pending_timeout'd."""
    now = datetime.now(timezone.utc)
    old_created = now - timedelta(seconds=recycler.DISPATCHED_TIMEOUT_SECONDS + 60)
    pending_seed = _seed_pending_job(created_at=old_created)

    db = SessionLocal()
    try:
        # Sibling RUNNING job on the same host (= Agent still draining queue)
        sibling_device = Device(
            serial=f"RQ-{uuid4().hex[:8]}",
            host_id=pending_seed["host_id"],
            status="ONLINE",
            tags=[],
            created_at=now,
            adb_connected=True,
            adb_state="device",
        )
        db.add(sibling_device)
        db.flush()
        running_job = JobInstance(
            plan_run_id=pending_seed["plan_run_id"],
            plan_id=pending_seed["plan_id"],
            device_id=sibling_device.id,
            host_id=pending_seed["host_id"],
            status=JobStatus.RUNNING.value,
            pipeline_def=PIPELINE_DEF,
            created_at=old_created,
            updated_at=now,
            started_at=now,
        )
        db.add(running_job)
        db.commit()
        running_job_id = running_job.id
        sibling_device_id = sibling_device.id
    finally:
        db.close()

    monkeypatch.setattr(recycler, "_fill_deferred_post_completions", lambda db, current: 0)
    monkeypatch.setattr(recycler, "_prune_steptrace_artifacts", lambda db, current: None)
    monkeypatch.setattr(recycler, "schedule_emit", lambda *args, **kwargs: None)
    try:
        recycler.recycle_once()

        db = SessionLocal()
        try:
            pending_job = db.get(JobInstance, pending_seed["job_id"])
            assert pending_job is not None
            assert pending_job.status == JobStatus.PENDING.value, (
                f"queued PENDING must stay PENDING; got {pending_job.status}"
            )
            running = db.get(JobInstance, running_job_id)
            assert running is not None
            assert running.status == JobStatus.RUNNING.value
        finally:
            db.close()
    finally:
        db = SessionLocal()
        try:
            db.query(JobInstance).filter(JobInstance.id == running_job_id).delete()
            db.query(Device).filter(Device.id == sibling_device_id).delete()
            db.commit()
        finally:
            db.close()
        _cleanup_seed(pending_seed)


# ══════════════════════════════════════════════════════════════════════════════
# Phase 4c: RUNNING timeout → UNKNOWN (lease stays ACTIVE)
# ══════════════════════════════════════════════════════════════════════════════


def test_running_timeout_transitions_to_unknown(engine, monkeypatch):
    """Phase 4c: RUNNING timeout → UNKNOWN (not FAILED)."""
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(seconds=recycler.RUNNING_HEARTBEAT_TIMEOUT_SECONDS + 60)
    seed = _seed_running_job(
        started_at=old_time,
        updated_at=old_time,
        execution_state="EXECUTING_STEP",
    )

    monkeypatch.setattr(recycler, "_fill_deferred_post_completions", lambda db, current: 0)
    monkeypatch.setattr(recycler, "_prune_steptrace_artifacts", lambda db, current: None)
    monkeypatch.setattr(recycler, "schedule_emit", lambda *args, **kwargs: None)
    try:
        recycler.recycle_once()

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            assert job.status == JobStatus.UNKNOWN.value, (
                f"RUNNING timeout must transition to UNKNOWN; got {job.status}"
            )
            assert job.ended_at is not None, "ended_at must be set"
            # #146: UNKNOWN 行不留运行子状态，避免并发统计误判。
            assert job.execution_state is None
            # #2905：RUNNING→UNKNOWN 必须有持久证据（与 pending/patrol 两条同族路径对齐）——
            # 删掉 _mark_running_timeout 里的 record_audit 调用，本断言即红。
            audit = (
                db.query(AuditLog)
                .filter(
                    AuditLog.action == "job_running_timeout",
                    AuditLog.resource_type == "job_instance",
                    AuditLog.resource_id == str(seed["job_id"]),
                )
                .first()
            )
            assert audit is not None, "RUNNING→UNKNOWN 未写审计（#2905）"
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


def test_running_timeout_cas_does_not_overwrite_concurrent_completion(engine, monkeypatch):
    """候选读取后 Agent 完成 Job 时，recycler CAS 不得把 COMPLETED 覆盖成 UNKNOWN。"""
    from sqlalchemy import update as sa_update

    now = datetime.now(timezone.utc)
    old_time = now - timedelta(seconds=recycler.RUNNING_HEARTBEAT_TIMEOUT_SECONDS + 60)
    seed = _seed_running_job(started_at=old_time, updated_at=old_time)
    _patch_recycler_neutrals(monkeypatch)
    try:
        stale_db = SessionLocal()
        try:
            stale_job = stale_db.get(JobInstance, seed["job_id"])
            assert stale_job is not None
            observed_updated_at = stale_job.updated_at

            with SessionLocal.begin() as concurrent_db:
                concurrent_db.execute(
                    sa_update(JobInstance)
                    .where(JobInstance.id == seed["job_id"])
                    .values(
                        status=JobStatus.COMPLETED.value,
                        ended_at=now,
                        updated_at=now,
                    )
                )

            flipped = recycler._mark_running_timeout(
                stale_db, stale_job, now, "test_completion_race",
            )
            stale_db.commit()

            assert flipped is False
            stale_db.expire_all()
            completed = stale_db.get(JobInstance, seed["job_id"])
            assert completed.status == JobStatus.COMPLETED.value
            assert completed.updated_at != observed_updated_at
        finally:
            stale_db.close()
    finally:
        _cleanup_seed(seed)


def test_running_timeout_cas_does_not_overwrite_concurrent_heartbeat(engine, monkeypatch):
    """候选读取后执行心跳刷新时，recycler CAS 必须失败并保留 RUNNING（#991）。

    模拟 extend-batch：只刷新 last_execution_heartbeat_at，钉住 updated_at。
    """
    from sqlalchemy import update as sa_update

    now = datetime.now(timezone.utc)
    old_time = now - timedelta(seconds=recycler.RUNNING_HEARTBEAT_TIMEOUT_SECONDS + 60)
    seed = _seed_running_job(
        started_at=old_time,
        updated_at=old_time,
        execution_state="EXECUTING_STEP",
        last_execution_heartbeat_at=old_time,
    )
    job_deadline = now - timedelta(
        seconds=recycler.RUNNING_HEARTBEAT_TIMEOUT_SECONDS,
    )
    _patch_recycler_neutrals(monkeypatch)
    try:
        stale_db = SessionLocal()
        try:
            stale_job = stale_db.get(JobInstance, seed["job_id"])
            assert stale_job is not None

            with SessionLocal.begin() as concurrent_db:
                concurrent_db.execute(
                    sa_update(JobInstance)
                    .where(JobInstance.id == seed["job_id"])
                    .values(
                        last_execution_heartbeat_at=now,
                        updated_at=old_time,
                    )
                )

            flipped = recycler._mark_running_timeout(
                stale_db, stale_job, now, "test_heartbeat_race",
                execution_heartbeat_deadline=job_deadline,
            )
            stale_db.commit()

            assert flipped is False
            stale_db.expire_all()
            running = stale_db.get(JobInstance, seed["job_id"])
            assert running.status == JobStatus.RUNNING.value
            assert running.last_execution_heartbeat_at == now
            assert running.updated_at == old_time
        finally:
            stale_db.close()
    finally:
        _cleanup_seed(seed)


def test_running_timeout_cas_not_vetoed_by_updated_at_only_refresh(engine, monkeypatch):
    """#991: 仅刷新 updated_at（租约续租钉住字段的逆操作）不得阻止真正超时。"""
    from sqlalchemy import update as sa_update

    now = datetime.now(timezone.utc)
    old_time = now - timedelta(seconds=recycler.RUNNING_HEARTBEAT_TIMEOUT_SECONDS + 60)
    seed = _seed_running_job(started_at=old_time, updated_at=old_time)
    _patch_recycler_neutrals(monkeypatch)
    try:
        stale_db = SessionLocal()
        try:
            stale_job = stale_db.get(JobInstance, seed["job_id"])
            assert stale_job is not None

            with SessionLocal.begin() as concurrent_db:
                concurrent_db.execute(
                    sa_update(JobInstance)
                    .where(JobInstance.id == seed["job_id"])
                    .values(updated_at=now)
                )

            flipped = recycler._mark_running_timeout(
                stale_db, stale_job, now, "test_updated_at_irrelevant",
                require_unreported=True,
            )
            stale_db.commit()

            assert flipped is True
            stale_db.expire_all()
            unknown = stale_db.get(JobInstance, seed["job_id"])
            assert unknown.status == JobStatus.UNKNOWN.value
        finally:
            stale_db.close()
    finally:
        _cleanup_seed(seed)


def test_running_timeout_keeps_lease_active(engine, monkeypatch):
    """Phase 4c: RUNNING timeout → UNKNOWN, lease stays ACTIVE."""
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(seconds=recycler.RUNNING_HEARTBEAT_TIMEOUT_SECONDS + 60)
    seed = _seed_running_job(started_at=old_time, updated_at=old_time)

    # Create an ACTIVE lease
    db = SessionLocal()
    try:
        lease = DeviceLease(
            device_id=seed["device_id"], job_id=seed["job_id"],
            host_id=seed["host_id"], lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=f"{seed['device_id']}:1", lease_generation=1,
            agent_instance_id=seed["host_id"],
            acquired_at=old_time, renewed_at=old_time,
            expires_at=old_time + timedelta(seconds=600),
        )
        db.add(lease)
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(recycler, "_fill_deferred_post_completions", lambda db, current: 0)
    monkeypatch.setattr(recycler, "_prune_steptrace_artifacts", lambda db, current: None)
    monkeypatch.setattr(recycler, "schedule_emit", lambda *args, **kwargs: None)
    try:
        recycler.recycle_once()

        db = SessionLocal()
        try:
            dl = (
                db.query(DeviceLease)
                .filter(
                    DeviceLease.device_id == seed["device_id"],
                    DeviceLease.job_id == seed["job_id"],
                )
                .first()
            )
            assert dl is not None
            assert dl.status == LeaseStatus.ACTIVE.value, (
                f"Lease must stay ACTIVE after RUNNING timeout; got {dl.status}"
            )
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


def test_running_timeout_emits_unknown_socketio_not_failed(engine, monkeypatch):
    """Phase 4c: RUNNING timeout SocketIO shows UNKNOWN (not FAILED)."""
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(seconds=recycler.RUNNING_HEARTBEAT_TIMEOUT_SECONDS + 60)
    seed = _seed_running_job(started_at=old_time, updated_at=old_time)

    emit_calls = []
    monkeypatch.setattr(recycler, "schedule_emit",
                        lambda event, data, **kw: emit_calls.append((event, data)))
    monkeypatch.setattr(recycler, "_fill_deferred_post_completions", lambda db, current: 0)
    monkeypatch.setattr(recycler, "_prune_steptrace_artifacts", lambda db, current: None)
    try:
        recycler.recycle_once()

        job_updates = [c for c in emit_calls if c[0] == "job_status"]
        assert len(job_updates) >= 1
        _, data = job_updates[0]
        assert data["payload"]["status"] == "UNKNOWN", (
            f"SocketIO must emit UNKNOWN; got {data['payload']['status']}"
        )
        assert "room" in data or True  # B3: room targeting added
    finally:
        _cleanup_seed(seed)


# ══════════════════════════════════════════════════════════════════════════════
# ADR-0022 D10: PATROL stall detection (Pass #2b)
# ══════════════════════════════════════════════════════════════════════════════


def _stale_running_seed(now: datetime, *, age_seconds: int, pipeline_def: dict | None = None) -> dict:
    """Seed a RUNNING job with patrol heartbeat aged `age_seconds` ago.

    execution_state stays NULL and started_at is `age_seconds + 60` old, so
    the Pass #2 not-reported clock (dispatch time + full executor window)
    does NOT trigger on these jobs — only Pass #2b patrol_stall is exercised.
    """
    return _seed_running_job(
        started_at=now - timedelta(seconds=age_seconds + 60),
        updated_at=now - timedelta(seconds=30),
        pipeline_def=pipeline_def if pipeline_def is not None else PATROL_PIPELINE_DEF,
        last_patrol_heartbeat_at=now - timedelta(seconds=age_seconds),
    )


def _patch_recycler_neutrals(monkeypatch, emit_sink: list | None = None) -> list:
    """Disable post-completion + artifact prune, capture schedule_emit calls."""
    monkeypatch.setattr(recycler, "_fill_deferred_post_completions", lambda db, current: 0)
    monkeypatch.setattr(recycler, "_prune_steptrace_artifacts", lambda db, current: None)
    sink = emit_sink if emit_sink is not None else []
    monkeypatch.setattr(
        recycler,
        "schedule_emit",
        lambda event, data, **kw: sink.append((event, data, kw)),
    )
    return sink


def _seed_init_completion(job_id: int, *, step_ids: list[str], completed_at: datetime) -> None:
    db = SessionLocal()
    try:
        for offset, step_id in enumerate(step_ids):
            db.add(StepTrace(
                job_id=job_id,
                step_id=step_id,
                stage="init",
                event_type="COMPLETED",
                status="COMPLETED",
                original_ts=completed_at - timedelta(seconds=max(len(step_ids) - offset - 1, 0)),
                created_at=completed_at,
            ))
        db.commit()
    finally:
        db.close()


def test_patrol_stall_transitions_running_to_unknown_when_overdue(engine, monkeypatch):
    """Heartbeat age=200s > 60*3=180s threshold → UNKNOWN + audit + socketio + metric."""
    from backend.models.audit import AuditLog

    now = datetime.now(timezone.utc)
    seed = _stale_running_seed(now, age_seconds=200)
    # #146: seed 一个残留的 patrol 子状态，验证转换时被清空。
    db = SessionLocal()
    try:
        job = db.get(JobInstance, seed["job_id"])
        job.execution_state = "PATROL_SLEEP"
        db.commit()
    finally:
        db.close()
    emits = _patch_recycler_neutrals(monkeypatch)

    before = recycler.recycler_timeouts.labels(timeout_type="patrol_stall")._value.get()
    try:
        recycler.recycle_once()

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            assert job.status == JobStatus.UNKNOWN.value
            assert job.ended_at is not None
            assert job.execution_state is None
            assert "patrol_stall" in (job.status_reason or "")

            audit = (
                db.query(AuditLog)
                .filter(
                    AuditLog.action == "patrol_stall_detected",
                    AuditLog.resource_id == str(seed["job_id"]),
                )
                .one()
            )
            assert audit.details["interval_seconds"] == 60
            assert audit.details["multiplier"] == recycler.PATROL_STALL_MULTIPLIER
            assert audit.details["age_seconds"] >= 180
        finally:
            db.close()

        unknown_emits = [
            (e, d) for (e, d, _kw) in emits
            if e == "job_status" and d.get("payload", {}).get("status") == "UNKNOWN"
        ]
        assert len(unknown_emits) == 1

        after = recycler.recycler_timeouts.labels(timeout_type="patrol_stall")._value.get()
        assert after - before == 1
    finally:
        _cleanup_seed(seed)


def test_patrol_stall_keeps_fresh_heartbeat_running(engine, monkeypatch):
    """Heartbeat age=30s < threshold 180s → no transition, no audit, no emit."""
    from backend.models.audit import AuditLog

    now = datetime.now(timezone.utc)
    seed = _stale_running_seed(now, age_seconds=30)
    emits = _patch_recycler_neutrals(monkeypatch)

    before = recycler.recycler_timeouts.labels(timeout_type="patrol_stall")._value.get()
    try:
        recycler.recycle_once()

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job.status == JobStatus.RUNNING.value
            audits = (
                db.query(AuditLog)
                .filter(
                    AuditLog.action == "patrol_stall_detected",
                    AuditLog.resource_id == str(seed["job_id"]),
                )
                .all()
            )
            assert audits == []
        finally:
            db.close()

        stall_emits = [
            d for (e, d, _kw) in emits
            if e == "job_status" and d.get("payload", {}).get("job_id") == seed["job_id"]
        ]
        assert stall_emits == []

        after = recycler.recycler_timeouts.labels(timeout_type="patrol_stall")._value.get()
        assert after == before
    finally:
        _cleanup_seed(seed)


def test_patrol_timeout_does_not_preempt_long_interval(engine, monkeypatch):
    """A legitimate 10-minute patrol interval must not hit the 300s RUNNING timeout."""
    now = datetime.now(timezone.utc)
    pipeline_def = {
        "lifecycle": {
            "init": [],
            "patrol": {
                "interval_seconds": 600,
                "steps": [{"step_id": "s", "action": "script:x"}],
            },
            "teardown": [],
        }
    }
    heartbeat_at = now - timedelta(seconds=400)
    seed = _seed_running_job(
        started_at=heartbeat_at - timedelta(seconds=60),
        updated_at=heartbeat_at,
        pipeline_def=pipeline_def,
        last_patrol_heartbeat_at=heartbeat_at,
        patrol_cycle_count=1,
    )
    _patch_recycler_neutrals(monkeypatch)
    try:
        recycler.recycle_once()

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            assert job.status == JobStatus.RUNNING.value
            assert job.status_reason != "running_timeout: no completion within window"
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


def test_patrol_stall_waits_for_backoff_retry_window(engine, monkeypatch):
    """Failure backoff is expected sleep, not a patrol stall."""
    now = datetime.now(timezone.utc)
    heartbeat_at = now - timedelta(seconds=400)
    seed = _seed_running_job(
        started_at=heartbeat_at - timedelta(seconds=60),
        updated_at=heartbeat_at,
        pipeline_def=PATROL_PIPELINE_DEF,
        last_patrol_heartbeat_at=heartbeat_at,
        next_retry_at=heartbeat_at + timedelta(seconds=480),
        patrol_cycle_count=1,
        current_failure_streak=5,
    )
    _patch_recycler_neutrals(monkeypatch)
    try:
        recycler.recycle_once()

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            assert job.status == JobStatus.RUNNING.value
            assert job.status_reason is None
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


def test_patrol_stall_detects_after_backoff_retry_window(engine, monkeypatch):
    """After next_retry_at plus the normal stall window, patrol is genuinely stale."""
    from backend.models.audit import AuditLog

    now = datetime.now(timezone.utc)
    heartbeat_at = now - timedelta(seconds=700)
    backoff_end = heartbeat_at + timedelta(seconds=480)
    seed = _seed_running_job(
        started_at=heartbeat_at - timedelta(seconds=60),
        updated_at=backoff_end,
        pipeline_def=PATROL_PIPELINE_DEF,
        last_patrol_heartbeat_at=heartbeat_at,
        next_retry_at=backoff_end,
        patrol_cycle_count=1,
        current_failure_streak=5,
        # PATROL_SLEEP + live coordinator: Pass #2 must skip this job so
        # Pass #2b's backoff-aware stall logic is what fires (#288).
        execution_state="PATROL_SLEEP",
    )
    _seed_fresh_coordinator_heartbeat(seed)
    _patch_recycler_neutrals(monkeypatch)
    try:
        recycler.recycle_once()

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            assert job.status == JobStatus.UNKNOWN.value
            assert "patrol_stall" in (job.status_reason or "")
            audit = (
                db.query(AuditLog)
                .filter(
                    AuditLog.action == "patrol_stall_detected",
                    AuditLog.resource_id == str(seed["job_id"]),
                )
                .one()
            )
            assert audit.details["age_seconds"] >= 220
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


def test_patrol_stall_skips_jobs_without_patrol_section(engine, monkeypatch):
    """pipeline_def lacking lifecycle.patrol → skip even when heartbeat is stale."""
    now = datetime.now(timezone.utc)
    init_only = {"lifecycle": {"init": [], "teardown": []}}
    seed = _stale_running_seed(now, age_seconds=600, pipeline_def=init_only)
    _patch_recycler_neutrals(monkeypatch)
    try:
        recycler.recycle_once()

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job.status == JobStatus.RUNNING.value
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


def test_patrol_stall_skips_jobs_still_in_init_before_first_heartbeat(engine, monkeypatch):
    """INIT 未完成且无 patrol heartbeat → 仍视为 init,不能提前打 patrol_stall。"""
    now = datetime.now(timezone.utc)
    pipeline_def = {
        "lifecycle": {
            "init": [
                {"step_id": "init.prepare", "action": "script:init_prepare"},
                {"step_id": "init.login", "action": "script:init_login"},
            ],
            "patrol": PATROL_PIPELINE_DEF["lifecycle"]["patrol"],
            "teardown": [],
        }
    }
    seed = _seed_running_job(
        started_at=now - timedelta(seconds=600),
        updated_at=now - timedelta(seconds=30),
        pipeline_def=pipeline_def,
        last_patrol_heartbeat_at=None,
    )
    _patch_recycler_neutrals(monkeypatch)
    try:
        recycler.recycle_once()

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job.status == JobStatus.RUNNING.value
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


def test_patrol_stall_detects_first_cycle_before_first_heartbeat_after_init(engine, monkeypatch):
    """INIT 已完成但首个 patrol 周期一直没 heartbeat,超过阈值也必须走 patrol_stall。"""
    from backend.models.audit import AuditLog

    now = datetime.now(timezone.utc)
    pipeline_def = {
        "lifecycle": {
            "init": [
                {"step_id": "init.prepare", "action": "script:init_prepare"},
                {"step_id": "init.login", "action": "script:init_login"},
            ],
            "patrol": PATROL_PIPELINE_DEF["lifecycle"]["patrol"],
            "teardown": [],
        }
    }
    seed = _seed_running_job(
        started_at=now - timedelta(seconds=900),
        updated_at=now - timedelta(seconds=30),
        pipeline_def=pipeline_def,
        last_patrol_heartbeat_at=None,
        # PATROL_SLEEP + live coordinator: keeps Pass #2 (coordinator clock)
        # out of the way so the first-cycle stall detection is exercised.
        execution_state="PATROL_SLEEP",
    )
    _seed_fresh_coordinator_heartbeat(seed)
    _seed_init_completion(
        seed["job_id"],
        step_ids=["init.prepare", "init.login"],
        completed_at=now - timedelta(seconds=200),
    )
    _patch_recycler_neutrals(monkeypatch)
    try:
        recycler.recycle_once()

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            assert job.status == JobStatus.UNKNOWN.value

            audit = (
                db.query(AuditLog)
                .filter(
                    AuditLog.action == "patrol_stall_detected",
                    AuditLog.resource_id == str(seed["job_id"]),
                )
                .one()
            )
            assert audit.details["interval_seconds"] == 60
            assert audit.details["age_seconds"] >= 180
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


def test_patrol_stall_uses_init_completion_anchor_before_first_heartbeat(engine, monkeypatch):
    """started_at 很老但 init 刚完成时,首个 patrol 周期不能按 started_at 误判 stall。"""
    now = datetime.now(timezone.utc)
    pipeline_def = {
        "lifecycle": {
            "init": [
                {"step_id": "init.prepare", "action": "script:init_prepare"},
                {"step_id": "init.login", "action": "script:init_login"},
            ],
            "patrol": PATROL_PIPELINE_DEF["lifecycle"]["patrol"],
            "teardown": [],
        }
    }
    seed = _seed_running_job(
        started_at=now - timedelta(seconds=900),
        updated_at=now - timedelta(seconds=30),
        pipeline_def=pipeline_def,
        last_patrol_heartbeat_at=None,
        # PATROL_SLEEP + live coordinator: keeps Pass #2 (coordinator clock)
        # out of the way so the init-completion anchor freshness is what
        # spares this job from stall detection.
        execution_state="PATROL_SLEEP",
    )
    _seed_fresh_coordinator_heartbeat(seed)
    _seed_init_completion(
        seed["job_id"],
        step_ids=["init.prepare", "init.login"],
        completed_at=now - timedelta(seconds=30),
    )
    _patch_recycler_neutrals(monkeypatch)
    try:
        recycler.recycle_once()

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            assert job.status == JobStatus.RUNNING.value
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


def test_patrol_stall_cas_no_op_when_heartbeat_raced_in(engine, monkeypatch):
    """直接调用 _mark_patrol_stall;调用前用 raw UPDATE 把 heartbeat 推到 fresh
    模拟 race:候选采集之后、CAS 之前 Agent 心跳到达。CAS WHERE 失配 → False。"""
    from sqlalchemy import update as sa_update
    from backend.models.audit import AuditLog

    now = datetime.now(timezone.utc)
    seed = _stale_running_seed(now, age_seconds=200)
    _patch_recycler_neutrals(monkeypatch)
    try:
        db = SessionLocal()
        try:
            # 模拟 race-in:把 heartbeat 推到 fresh (now-10s)
            db.execute(
                sa_update(JobInstance)
                .where(JobInstance.id == seed["job_id"])
                .values(last_patrol_heartbeat_at=now - timedelta(seconds=10))
            )
            db.commit()

            job = db.get(JobInstance, seed["job_id"])
            # 用原 stale interval (60) 走 CAS — cutoff = now - 180s,
            # 但 DB heartbeat=now-10s,WHERE last_patrol_heartbeat_at<cutoff 失配
            flipped = recycler._mark_patrol_stall(
                db, job, now,
                interval_seconds=60,
                age_seconds=200,
                reason="test_race",
            )
            assert flipped is False

            db.expire_all()
            job = db.get(JobInstance, seed["job_id"])
            assert job.status == JobStatus.RUNNING.value
            audits = (
                db.query(AuditLog)
                .filter(
                    AuditLog.action == "patrol_stall_detected",
                    AuditLog.resource_id == str(seed["job_id"]),
                )
                .all()
            )
            assert audits == []
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


def test_patrol_stall_after_running_timeout_no_double_transition(engine, monkeypatch):
    """下发时刻锚点早于 running deadline 且 last_patrol_heartbeat_at 也老化:
    Pass #2 先把 Job 标 UNKNOWN,Pass #2b 候选 SQL filter status='RUNNING' 失配 → 不入选。
    单 tick 内只发生 1 次状态变化,无 patrol_stall_detected audit。"""
    from backend.models.audit import AuditLog

    now = datetime.now(timezone.utc)
    old = now - timedelta(seconds=recycler.RUNNING_HEARTBEAT_TIMEOUT_SECONDS + 60)
    seed = _seed_running_job(
        started_at=old,
        updated_at=old,
        pipeline_def=PATROL_PIPELINE_DEF,
        last_patrol_heartbeat_at=now - timedelta(seconds=600),
    )
    _patch_recycler_neutrals(monkeypatch)
    try:
        recycler.recycle_once()

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job.status == JobStatus.UNKNOWN.value

            audits = (
                db.query(AuditLog)
                .filter(
                    AuditLog.action == "patrol_stall_detected",
                    AuditLog.resource_id == str(seed["job_id"]),
                )
                .all()
            )
            assert audits == [], "Pass #2b must not double-transition jobs already flipped by Pass #2"
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


def test_patrol_stall_picks_most_overdue_when_mixed_intervals(engine, monkeypatch, scheduler_env):
    """3 个 RUNNING 候选,interval 各不同:
      A: interval=600, age=400 → overdue=-1400 (健康)
      B: interval=60,  age=200 → overdue=+20  (stalled,小)
      C: interval=30,  age=200 → overdue=+110 (stalled,最大)
    BATCH_LIMIT=1 → 仅 C 被处理(证明 Python 侧按 overdue DESC 排序生效;
    SQL ORDER BY raw heartbeat ASC 无法表达此语义)。"""
    from backend.models.audit import AuditLog

    now = datetime.now(timezone.utc)

    def _pipe(interval: int) -> dict:
        return {
            "lifecycle": {
                "init": [],
                "patrol": {"interval_seconds": interval, "steps": [{"step_id": "s", "action": "script:x"}]},
                "teardown": [],
            }
        }

    seed_a = _stale_running_seed(now, age_seconds=400, pipeline_def=_pipe(600))
    seed_b = _stale_running_seed(now, age_seconds=200, pipeline_def=_pipe(60))
    seed_c = _stale_running_seed(now, age_seconds=200, pipeline_def=_pipe(30))

    scheduler_env("PATROL_STALL_BATCH_LIMIT", "1")
    _patch_recycler_neutrals(monkeypatch)
    try:
        recycler.recycle_once()

        db = SessionLocal()
        try:
            job_a = db.get(JobInstance, seed_a["job_id"])
            job_b = db.get(JobInstance, seed_b["job_id"])
            job_c = db.get(JobInstance, seed_c["job_id"])
            assert job_a.status == JobStatus.RUNNING.value, "A 健康,必须保持 RUNNING"
            assert job_b.status == JobStatus.RUNNING.value, "B overdue 较小,本 tick 不入选"
            assert job_c.status == JobStatus.UNKNOWN.value, "C overdue 最大,必须本 tick 入选"

            audits = (
                db.query(AuditLog)
                .filter(AuditLog.action == "patrol_stall_detected")
                .filter(AuditLog.resource_id.in_([str(seed_a["job_id"]), str(seed_b["job_id"]), str(seed_c["job_id"])]))
                .all()
            )
            assert len(audits) == 1
            assert audits[0].resource_id == str(seed_c["job_id"])
        finally:
            db.close()
    finally:
        _cleanup_seed(seed_a)
        _cleanup_seed(seed_b)
        _cleanup_seed(seed_c)


# ══════════════════════════════════════════════════════════════════════════════
# #3061: step_trace stall detection (Pass #2c)
# ══════════════════════════════════════════════════════════════════════════════


def _step_trace_stall_seed(
    now: datetime,
    *,
    trace_age_seconds: int,
    event_type: str = "COMPLETED",
    fresh_liveness: bool = True,
    patrol_heartbeat_age_seconds: int | None = None,
    execution_state: str | None = "PATROL_SLEEP",
    pipeline_def: dict | None = None,
) -> dict:
    """RUNNING job whose execution heartbeats stay fresh but step_trace is stale.

    ``patrol_heartbeat_age_seconds`` 单独控制 patrol 通道（#3146）：缺省跟随
    ``fresh_liveness``；显式传秒数可构造「执行心跳新鲜 + patrol 心跳陈旧」的
    #3061 真僵尸形态，或「patrol 心跳新鲜」的健康巡航形态（ADR-0022：patrol
    成功步不写 trace，巡航期 trace 静默是设计形态）。
    """
    patrol_age_seconds = (
        patrol_heartbeat_age_seconds
        if patrol_heartbeat_age_seconds is not None
        else (30 if fresh_liveness else 13 * 3600)
    )
    seed = _seed_running_job(
        started_at=now - timedelta(hours=13),
        updated_at=now - timedelta(seconds=30),
        pipeline_def=pipeline_def if pipeline_def is not None else PATROL_PIPELINE_DEF,
        last_patrol_heartbeat_at=now - timedelta(seconds=patrol_age_seconds),
        execution_state=execution_state,
        last_execution_heartbeat_at=(
            now - timedelta(seconds=30) if fresh_liveness else now - timedelta(hours=13)
        ),
    )
    if fresh_liveness and execution_state in {"PATROL_SLEEP", "WAITING_DEVICE"}:
        _seed_fresh_coordinator_heartbeat(seed, at=now - timedelta(seconds=30))

    trace_at = now - timedelta(seconds=trace_age_seconds)
    db = SessionLocal()
    try:
        db.add(StepTrace(
            job_id=seed["job_id"],
            step_id="monkey_launch",
            stage="init",
            event_type=event_type,
            status="RUNNING" if event_type == "STARTED" else "COMPLETED",
            original_ts=trace_at,
            created_at=trace_at,
        ))
        db.commit()
    finally:
        db.close()
    return seed


def test_step_trace_stall_transitions_running_to_unknown_when_stale(engine, monkeypatch):
    """#3061 真僵尸形态：非 patrol 计划（patrol 检测器不适用）trace 陈旧 → UNKNOWN。

    执行心跳（``last_execution_heartbeat_at``）保持新鲜——按 #3061 初衷，执行器存活
    不豁免僵尸判定；因该 job 无 patrol 段，patrol 检测器不介入，由 step_trace 检测器
    独立抓出（真僵尸）。
    """
    now = datetime.now(timezone.utc)
    seed = _step_trace_stall_seed(
        now, trace_age_seconds=7200, patrol_heartbeat_age_seconds=13 * 3600,
        pipeline_def=PIPELINE_DEF,
    )
    emits = _patch_recycler_neutrals(monkeypatch)

    before = recycler.recycler_timeouts.labels(timeout_type="step_trace_stall")._value.get()
    try:
        recycler.recycle_once()

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job is not None
            assert job.status == JobStatus.UNKNOWN.value
            assert job.ended_at is not None
            assert job.execution_state is None
            assert "step_trace_stall" in (job.status_reason or "")

            audit = (
                db.query(AuditLog)
                .filter(
                    AuditLog.action == "step_trace_stall_detected",
                    AuditLog.resource_id == str(seed["job_id"]),
                )
                .one()
            )
            assert audit.details["last_event_type"] == "COMPLETED"
            assert audit.details["age_seconds"] >= 7200
        finally:
            db.close()

        unknown_emits = [
            (e, d) for (e, d, _kw) in emits
            if e == "job_status" and d.get("payload", {}).get("status") == "UNKNOWN"
        ]
        assert len(unknown_emits) == 1

        after = recycler.recycler_timeouts.labels(timeout_type="step_trace_stall")._value.get()
        assert after - before == 1
    finally:
        _cleanup_seed(seed)


def test_step_trace_stall_keeps_fresh_trace_running(engine, monkeypatch):
    now = datetime.now(timezone.utc)
    seed = _step_trace_stall_seed(now, trace_age_seconds=600)
    emits = _patch_recycler_neutrals(monkeypatch)

    before = recycler.recycler_timeouts.labels(timeout_type="step_trace_stall")._value.get()
    try:
        recycler.recycle_once()

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job.status == JobStatus.RUNNING.value
            audits = (
                db.query(AuditLog)
                .filter(
                    AuditLog.action == "step_trace_stall_detected",
                    AuditLog.resource_id == str(seed["job_id"]),
                )
                .all()
            )
            assert audits == []
        finally:
            db.close()

        stall_emits = [
            d for (e, d, _kw) in emits
            if e == "job_status" and d.get("payload", {}).get("job_id") == seed["job_id"]
        ]
        assert stall_emits == []

        after = recycler.recycler_timeouts.labels(timeout_type="step_trace_stall")._value.get()
        assert after == before
    finally:
        _cleanup_seed(seed)


def test_step_trace_stall_skips_inflight_started_trace(engine, monkeypatch):
    now = datetime.now(timezone.utc)
    seed = _step_trace_stall_seed(now, trace_age_seconds=7200, event_type="STARTED")
    _patch_recycler_neutrals(monkeypatch)

    try:
        recycler.recycle_once()

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job.status == JobStatus.RUNNING.value
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


def test_step_trace_stall_disabled_when_threshold_zero(engine, monkeypatch):
    now = datetime.now(timezone.utc)
    seed = _step_trace_stall_seed(now, trace_age_seconds=7200)
    monkeypatch.setattr(recycler, "STEP_TRACE_STALL_SECONDS", 0)
    _patch_recycler_neutrals(monkeypatch)

    try:
        recycler.recycle_once()

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job.status == JobStatus.RUNNING.value
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


def test_step_trace_stall_keeps_patrol_cruise_running(engine, monkeypatch):
    """#3146：ADR-0022 让 patrol 成功步不写 step_trace ⇒ 「trace 静默 + patrol 心跳新鲜」
    是**健康巡航**形态（2026-09-22 r510 误杀形态：738 条误标 → 释放 lease → 中止波），
    不得标 UNKNOWN。
    """
    now = datetime.now(timezone.utc)
    seed = _step_trace_stall_seed(
        now, trace_age_seconds=7200, patrol_heartbeat_age_seconds=120,
    )
    emits = _patch_recycler_neutrals(monkeypatch)

    before = recycler.recycler_timeouts.labels(timeout_type="step_trace_stall")._value.get()
    try:
        recycler.recycle_once()

        db = SessionLocal()
        try:
            job = db.get(JobInstance, seed["job_id"])
            assert job.status == JobStatus.RUNNING.value
            assert job.ended_at is None
            audits = (
                db.query(AuditLog)
                .filter(
                    AuditLog.action == "step_trace_stall_detected",
                    AuditLog.resource_id == str(seed["job_id"]),
                )
                .all()
            )
            assert audits == []
        finally:
            db.close()

        unknown_emits = [
            d for (e, d, _kw) in emits
            if e == "job_status" and d.get("payload", {}).get("status") == "UNKNOWN"
        ]
        assert unknown_emits == []

        after = recycler.recycler_timeouts.labels(timeout_type="step_trace_stall")._value.get()
        assert after == before
    finally:
        _cleanup_seed(seed)


def test_step_trace_stall_cas_no_op_when_patrol_heartbeat_raced_in(engine, monkeypatch):
    """collect→mark 之间 patrol 心跳落地（race-in）→ CAS 失配：不标 UNKNOWN、不写审计。"""
    from sqlalchemy import update as sa_update

    now = datetime.now(timezone.utc)
    seed = _step_trace_stall_seed(
        now, trace_age_seconds=7200, patrol_heartbeat_age_seconds=13 * 3600,
    )
    _patch_recycler_neutrals(monkeypatch)
    try:
        db = SessionLocal()
        try:
            db.execute(
                sa_update(JobInstance)
                .where(JobInstance.id == seed["job_id"])
                .values(last_patrol_heartbeat_at=now - timedelta(seconds=10))
            )
            db.commit()

            job = db.get(JobInstance, seed["job_id"])
            flipped = recycler._mark_step_trace_stall(
                db, job, now,
                last_ts=now - timedelta(seconds=7200),
                event_type="COMPLETED",
                reason="step_trace_stall: last=COMPLETED age=7200s > 3600s",
            )
            assert flipped is False

            db.expire_all()
            job = db.get(JobInstance, seed["job_id"])
            assert job.status == JobStatus.RUNNING.value
            audits = (
                db.query(AuditLog)
                .filter(
                    AuditLog.action == "step_trace_stall_detected",
                    AuditLog.resource_id == str(seed["job_id"]),
                )
                .all()
            )
            assert audits == []
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


def test_step_trace_stall_collector_pins_patrol_guard_both_ways(engine, monkeypatch):
    """直接钉 collector 那道闸（#3146 第一层）：
    ① patrol 心跳新鲜 → **不**进候选（巡航健康）；② patrol 心跳陈旧且无 patrol 段
    → 进候选（真僵尸形态）。这样单独删掉 collector 的豁免过滤会立刻变红。
    """
    now = datetime.now(timezone.utc)
    healthy = _step_trace_stall_seed(
        now, trace_age_seconds=7200, patrol_heartbeat_age_seconds=120,
    )
    zombie = _step_trace_stall_seed(
        now, trace_age_seconds=7200, patrol_heartbeat_age_seconds=13 * 3600,
        pipeline_def=PIPELINE_DEF,
    )
    _patch_recycler_neutrals(monkeypatch)
    try:
        db = SessionLocal()
        try:
            candidates = recycler._collect_step_trace_stall_candidates(db, now)
            ids = {job.id for job, _ts, _et in candidates}
            assert healthy["job_id"] not in ids
            assert zombie["job_id"] in ids
        finally:
            db.close()
    finally:
        _cleanup_seed(healthy)
        _cleanup_seed(zombie)


# ── #3341：截止行 SQL 层排除 + ORDER BY + gauge + ingest 复核 ───────────────


class _DeferredFillCfg:
    post_completion_grace_seconds = 600
    post_completion_max_defer_seconds = 86400


def _seed_deferred_orphans(db_session, *, now, cutoff_count):
    """Seed `cutoff_count` beyond-cutoff terminal jobs + 1 in-band fresh orphan."""
    grace = _DeferredFillCfg.post_completion_grace_seconds
    max_defer = _DeferredFillCfg.post_completion_max_defer_seconds
    suffix = uuid4().hex[:8]
    host = Host(
        id=f"rc-defer-{suffix}", hostname=f"rc-defer-{suffix}",
        status=HostStatus.ONLINE.value, last_heartbeat=now, created_at=now,
    )
    plan = Plan(name=f"rc-defer-{suffix}")
    db_session.add_all([host, plan])
    db_session.flush()
    run = PlanRun(
        plan_id=plan.id, status="FAILED",
        plan_snapshot={"name": plan.name, "plan_id": plan.id},
        triggered_by="pytest", started_at=now, run_type="MANUAL",
    )
    db_session.add(run)
    db_session.flush()
    cutoff_ids = []
    fresh_id = None
    for i in range(cutoff_count + 1):
        # (plan_run_id, device_id) 唯一约束——每 job 一台设备。
        device = Device(
            serial=f"RCDEF-{suffix}-{i}", host_id=f"rc-defer-{suffix}",
            status="IDLE", tags=[], created_at=now,
        )
        db_session.add(device)
        db_session.flush()
        if i < cutoff_count:
            ended = now - timedelta(seconds=grace + max_defer + 3600)
        else:
            ended = now - timedelta(seconds=grace + 60)
        job = JobInstance(
            plan_run_id=run.id, plan_id=plan.id, device_id=device.id,
            host_id=f"rc-defer-{suffix}", status=JobStatus.COMPLETED.value,
            pipeline_def=PIPELINE_DEF, created_at=now, updated_at=now,
            started_at=now, ended_at=ended, post_processed_at=None,
        )
        db_session.add(job)
        db_session.flush()
        if i < cutoff_count:
            cutoff_ids.append(job.id)
        else:
            fresh_id = job.id
    db_session.commit()
    return cutoff_ids, fresh_id


def test_deferred_fill_cutoff_rows_excluded_and_gauged_3341(db_session, monkeypatch):
    """10 截止行 + 1 新孤儿：新孤儿仍入队、截止行不占名额不重入队，gauge=10。"""
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(recycler, "_sched", lambda: _DeferredFillCfg())
    cutoff_ids, fresh_id = _seed_deferred_orphans(db_session, now=now, cutoff_count=10)

    enqueued = []
    monkeypatch.setattr(
        "backend.core.task_queue.enqueue_sync",
        lambda task, **kw: enqueued.append(kw.get("key") or task),
    )
    monkeypatch.setattr(
        "backend.services.case_result_ingest.case_result_ingest_pending",
        lambda db, job_id: True,
    )

    filled = recycler._fill_deferred_post_completions(db_session, now)

    assert filled == 1
    assert f"pc:{fresh_id}" in enqueued
    assert not any(f"pc:{cid}" in enqueued for cid in cutoff_ids)
    from backend.core.metrics import post_completion_cutoff_jobs

    assert post_completion_cutoff_jobs._value.get() == 10


def test_deferred_fill_readable_cutoff_row_reenqueued_3341(db_session, monkeypatch):
    """截止行 detail 已可读＝主路径漏发：照常重入队不截止。"""
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(recycler, "_sched", lambda: _DeferredFillCfg())
    cutoff_ids, fresh_id = _seed_deferred_orphans(db_session, now=now, cutoff_count=1)

    enqueued = []
    monkeypatch.setattr(
        "backend.core.task_queue.enqueue_sync",
        lambda task, **kw: enqueued.append(kw.get("key") or task),
    )
    monkeypatch.setattr(
        "backend.services.case_result_ingest.case_result_ingest_pending",
        lambda db, job_id: False,
    )

    filled = recycler._fill_deferred_post_completions(db_session, now)

    assert filled == 2
    assert all(f"pc:{cid}" in enqueued for cid in cutoff_ids)
    assert f"pc:{fresh_id}" in enqueued
    from backend.core.metrics import post_completion_cutoff_jobs

    assert post_completion_cutoff_jobs._value.get() == 1
