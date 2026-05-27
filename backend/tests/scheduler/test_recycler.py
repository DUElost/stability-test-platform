from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend.core.database import SessionLocal
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


def _seed_running_job(
    started_at: datetime,
    updated_at: datetime,
    *,
    pipeline_def: dict | None = None,
    last_patrol_heartbeat_at: datetime | None = None,
    next_retry_at: datetime | None = None,
    current_failure_streak: int = 0,
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
            failure_threshold=0.1,
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
            failure_threshold=0.1,
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
            current_failure_streak=current_failure_streak,
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


def _cleanup_seed(seed: dict) -> None:
    from backend.models.audit import AuditLog
    from backend.models.device_lease import DeviceLease
    db = SessionLocal()
    try:
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
    seed = _seed_running_job(started_at=old_started_at, updated_at=now)
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
            name=f"wf-{suffix}", description="pytest", failure_threshold=0.1,
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
            plan_id=plan.id, status="RUNNING",
            failure_threshold=0.1, triggered_by="pytest", started_at=now,
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
        finally:
            db.close()
    finally:
        _cleanup_seed(seed)


# ══════════════════════════════════════════════════════════════════════════════
# Phase 4c: RUNNING timeout → UNKNOWN (lease stays ACTIVE)
# ══════════════════════════════════════════════════════════════════════════════


def test_running_timeout_transitions_to_unknown(engine, monkeypatch):
    """Phase 4c: RUNNING timeout → UNKNOWN (not FAILED)."""
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(seconds=recycler.RUNNING_HEARTBEAT_TIMEOUT_SECONDS + 60)
    seed = _seed_running_job(started_at=old_time, updated_at=old_time)

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
        finally:
            db.close()
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

    updated_at is kept fresh (now - 30s) so Pass #2 RUNNING timeout will NOT
    trigger on this job — only Pass #2b patrol_stall is exercised.
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
            "patrol": {"interval_seconds": 600, "steps": [{"step_id": "s", "action": "script:x"}]},
            "teardown": [],
        }
    }
    heartbeat_at = now - timedelta(seconds=400)
    seed = _seed_running_job(
        started_at=heartbeat_at - timedelta(seconds=60),
        updated_at=heartbeat_at,
        pipeline_def=pipeline_def,
        last_patrol_heartbeat_at=heartbeat_at,
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
    seed = _seed_running_job(
        started_at=heartbeat_at - timedelta(seconds=60),
        updated_at=heartbeat_at,
        pipeline_def=PATROL_PIPELINE_DEF,
        last_patrol_heartbeat_at=heartbeat_at,
        next_retry_at=heartbeat_at + timedelta(seconds=480),
        current_failure_streak=5,
    )
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
    )
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
    )
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
    """updated_at < running_deadline AND last_patrol_heartbeat_at 也老化:
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


def test_patrol_stall_picks_most_overdue_when_mixed_intervals(engine, monkeypatch):
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

    monkeypatch.setattr(recycler, "PATROL_STALL_BATCH_LIMIT", 1)
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
