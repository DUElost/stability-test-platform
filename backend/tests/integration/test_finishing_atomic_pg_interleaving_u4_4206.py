"""ADR-0065 / #4206 U4 — PostgreSQL interleaving C4–C8 (atomic_commit handshake).

Pattern: ``backend/tests/services/test_device_return_lock_order_3728.py``.
Filename intentionally omits ``guard`` (static-guard PR path contract).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from backend.agent.contracts.capabilities import (
    ATOMIC_COMMIT,
    FINISHING_GUARD_V2,
    FORCE_ABORT,
)
from backend.agent.contracts.finishing_guard import GUARD_STATE_PRE_REGISTERED
from backend.core.database import AsyncSessionLocal, SessionLocal, async_engine
from backend.models.device_lease import DeviceLease
from backend.models.device_return_debt import DeviceReturnDebt
from backend.models.enums import JobStatus, LeaseStatus, LeaseType
from backend.models.finishing_guard_admission import FinishingGuardAdmission
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services import device_return as dr
from backend.services.agent_completion import _RunCompleteIn, complete_agent_job
from backend.services.errors import Conflict
from backend.services.finishing_guard import (
    ActivatePermitIn,
    FinishingGuardError,
    PreRegisterIn,
    activate_permit_sync,
    close_admission_sync,
    count_active_atomic_guards_sync,
    pre_register_atomic_commit_sync,
)
from backend.services.plan_run_abort import PlanRunAbortError, abort_plan_run
from backend.tasks.session_watchdog import _reconcile_host_heartbeat_timeouts

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
IS_PG = TEST_DATABASE_URL.startswith("postgresql")
pytestmark = pytest.mark.skipif(not IS_PG, reason="U4 interleaving requires PostgreSQL")

def _fresh_now() -> datetime:
    """Per-call wall clock — module-level stamps go stale under long CI suites
    (HOST_HEARTBEAT_TIMEOUT_SECONDS=300s) and trip ``host_not_online_fresh``.
    """
    return datetime.now(timezone.utc)


def _set_timeouts(db):
    db.execute(text("SET LOCAL deadlock_timeout = '200ms'"))
    db.execute(text("SET LOCAL lock_timeout = '5s'"))


def _ensure_admission_row(db) -> None:
    row = db.get(FinishingGuardAdmission, 1)
    if row is None:
        db.add(FinishingGuardAdmission(id=1, admission_open=True))
    else:
        row.admission_open = True
    db.flush()


def _seed_running_atomic_job(
    db,
    *,
    unknown: bool = False,
) -> tuple[JobInstance, PlanRun, Host, Device]:
    _ensure_admission_row(db)
    now = _fresh_now()
    suffix = uuid4().hex[:8]
    host = Host(
        id=f"fg-{suffix}",
        hostname=f"fg-{suffix}",
        status="ONLINE",
        last_heartbeat=now,
        created_at=now,
        boot_id=f"boot-{suffix}",
        agent_capabilities=[FINISHING_GUARD_V2, ATOMIC_COMMIT, FORCE_ABORT],
    )
    device = Device(
        serial=f"FG-{suffix}",
        host_id=host.id,
        status="ONLINE",
        tags=[],
        created_at=now,
        last_seen=now,
        lease_generation=1,
    )
    plan = Plan(name=f"fg-{suffix}", description="u4", created_by="test")
    db.add_all([host, device, plan])
    db.flush()
    run = PlanRun(
        plan_id=plan.id,
        status="RUNNING",
        plan_snapshot={
            "name": plan.name,
            "plan_id": plan.id,
            "steps": [
                {
                    "stage": "init",
                    "step_key": "flash",
                    "script_name": "flash_firmware",
                    "timeout_seconds": 1800,
                    "atomic_group": "flash_atomic",
                    "enabled": True,
                    "sort_order": 0,
                },
                {
                    "stage": "init",
                    "step_key": "oobe_skip",
                    "script_name": "oobe_skip",
                    "timeout_seconds": 300,
                    "atomic_group": "flash_atomic",
                    "enabled": True,
                    "sort_order": 1,
                },
            ],
        },
        run_type="MANUAL",
        triggered_by="test",
        started_at=now,
        total_job_count=1,
    )
    db.add(run)
    db.flush()
    job = JobInstance(
        plan_run_id=run.id,
        plan_id=plan.id,
        device_id=device.id,
        host_id=host.id,
        status=JobStatus.UNKNOWN.value if unknown else JobStatus.RUNNING.value,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        negotiated_capabilities={
            FINISHING_GUARD_V2: True,
            ATOMIC_COMMIT: True,
            FORCE_ABORT: True,
        },
        started_at=now,
    )
    db.add(job)
    db.flush()
    lease = DeviceLease(
        device_id=device.id,
        job_id=job.id,
        host_id=host.id,
        lease_type=LeaseType.JOB.value,
        status=LeaseStatus.ACTIVE.value,
        fencing_token=f"{device.id}:1",
        lease_generation=1,
        agent_instance_id=host.id,
        acquired_at=now,
        renewed_at=now,
        expires_at=now + timedelta(hours=1),
    )
    db.add(lease)
    db.commit()
    for row in (host, device, job, run, lease):
        db.refresh(row)
    return job, run, host, device


class TestC4PreRegisterAbort:
    def test_abort_first_then_pre_register_rejects(self, db_session):
        job, run, _host, _dev = _seed_running_atomic_job(db_session)
        with SessionLocal() as db:
            _set_timeouts(db)
            abort_plan_run(run.id, db=db, reason="abort-first", force=False)
            db.commit()
        with SessionLocal() as db:
            _set_timeouts(db)
            with pytest.raises(FinishingGuardError) as exc:
                pre_register_atomic_commit_sync(
                    db,
                    job_id=job.id,
                    payload=PreRegisterIn(
                        request_id="req-abort-first",
                        min_critical_section_seconds=1,
                    ),
                )
            assert exc.value.code in {
                "abort_already_committed",
                "force_abort_pending",
            }

    def test_pre_register_first_then_abort_sees_guard(self, db_session):
        job, run, _host, _dev = _seed_running_atomic_job(db_session)
        with SessionLocal() as db:
            _set_timeouts(db)
            pre_register_atomic_commit_sync(
                db,
                job_id=job.id,
                payload=PreRegisterIn(
                    request_id="req-pre-first",
                    min_critical_section_seconds=1,
                ),
            )
            db.commit()
        with SessionLocal() as db:
            _set_timeouts(db)
            summary = abort_plan_run(run.id, db=db, reason="after-pre", force=False)
            db.commit()
        assert job.id in summary.abort_requested_jobs
        with SessionLocal() as db:
            j = db.get(JobInstance, job.id)
            assert j.finishing_guard["guard_state"] == GUARD_STATE_PRE_REGISTERED
            assert j.finishing_guard["request_id"] == "req-pre-first"

    def test_concurrent_pre_register_and_abort_no_deadlock(self, db_session):
        job, run, _host, _dev = _seed_running_atomic_job(db_session)
        job_id, run_id = job.id, run.id
        outcomes: dict[str, object] = {}

        def do_abort():
            with SessionLocal() as db:
                _set_timeouts(db)
                abort_plan_run(run_id, db=db, reason="race", force=False)
                db.commit()
                outcomes["abort"] = "ok"

        def do_pre():
            with SessionLocal() as db:
                _set_timeouts(db)
                try:
                    pre_register_atomic_commit_sync(
                        db,
                        job_id=job_id,
                        payload=PreRegisterIn(
                            request_id="req-race",
                            min_critical_section_seconds=1,
                        ),
                    )
                    db.commit()
                    outcomes["pre"] = "ok"
                except FinishingGuardError as exc:
                    db.rollback()
                    outcomes["pre"] = exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(do_abort)
            f2 = pool.submit(do_pre)
            try:
                f1.result(timeout=20)
                f2.result(timeout=20)
            except FuturesTimeout as exc:
                raise AssertionError("C4 concurrent path hung (possible deadlock)") from exc
        assert outcomes.get("abort") == "ok"
        assert "pre" in outcomes


class TestC5ActivatePermitWatchdog:
    def test_activate_grants_when_host_fresh(self, db_session):
        job, _run, host, _dev = _seed_running_atomic_job(db_session)
        with SessionLocal() as db:
            pre_register_atomic_commit_sync(
                db,
                job_id=job.id,
                payload=PreRegisterIn(
                    request_id="req-c5",
                    current_step_key="flash",
                    min_critical_section_seconds=1,
                ),
            )
            # Refresh heartbeat immediately before activate so long CI suites
            # cannot age the seed stamp past HOST_HEARTBEAT_TIMEOUT_SECONDS.
            h = db.get(Host, host.id)
            h.last_heartbeat = _fresh_now()
            db.commit()
        with SessionLocal() as db:
            out = activate_permit_sync(
                db,
                job_id=job.id,
                host_id=host.id,
                payload=ActivatePermitIn(
                    request_id="req-c5",
                    client_boot_id=host.boot_id or "",
                ),
            )
            db.commit()
            j = db.get(JobInstance, job.id)
            guard = j.finishing_guard
        assert out["status"] == "granted"
        assert "lease_remaining_seconds" in out
        assert "host_heartbeat_remaining_seconds" in out
        assert guard["D_atomic"] == min(guard["D_steps"], guard["D_cap"], guard["D_run"])

    def test_stale_host_heartbeat_denies_activate(self, db_session):
        job, _run, host, _dev = _seed_running_atomic_job(db_session)
        with SessionLocal() as db:
            pre_register_atomic_commit_sync(
                db,
                job_id=job.id,
                payload=PreRegisterIn(
                    request_id="req-c5-stale",
                    min_critical_section_seconds=1,
                ),
            )
            h = db.get(Host, host.id)
            h.last_heartbeat = _fresh_now() - timedelta(hours=2)
            db.commit()
        with SessionLocal() as db:
            with pytest.raises(FinishingGuardError) as exc:
                activate_permit_sync(
                    db,
                    job_id=job.id,
                    host_id=host.id,
                    payload=ActivatePermitIn(
                        request_id="req-c5-stale",
                        client_boot_id=host.boot_id or "",
                    ),
                )
            assert exc.value.code == "host_not_online_fresh"

    def test_boot_id_mismatch_denies_activate(self, db_session):
        job, _run, host, _dev = _seed_running_atomic_job(db_session)
        with SessionLocal() as db:
            pre_register_atomic_commit_sync(
                db,
                job_id=job.id,
                payload=PreRegisterIn(
                    request_id="req-c5-boot",
                    min_critical_section_seconds=1,
                ),
            )
            h = db.get(Host, host.id)
            h.last_heartbeat = _fresh_now()
            db.commit()
        with SessionLocal() as db:
            with pytest.raises(FinishingGuardError) as exc:
                activate_permit_sync(
                    db,
                    job_id=job.id,
                    host_id=host.id,
                    payload=ActivatePermitIn(
                        request_id="req-c5-boot",
                        client_boot_id="boot-other-instance",
                    ),
                )
            assert exc.value.code == "host_not_online_fresh"

    def test_expired_job_lease_denies_activate(self, db_session):
        job, _run, host, device = _seed_running_atomic_job(db_session)
        with SessionLocal() as db:
            pre_register_atomic_commit_sync(
                db,
                job_id=job.id,
                payload=PreRegisterIn(
                    request_id="req-c5-lease",
                    min_critical_section_seconds=1,
                ),
            )
            h = db.get(Host, host.id)
            h.last_heartbeat = _fresh_now()
            lease = db.execute(
                select(DeviceLease).where(
                    DeviceLease.device_id == device.id,
                    DeviceLease.job_id == job.id,
                    DeviceLease.status == LeaseStatus.ACTIVE.value,
                )
            ).scalars().one()
            lease.expires_at = _fresh_now() - timedelta(seconds=1)
            db.commit()
        with SessionLocal() as db:
            with pytest.raises(FinishingGuardError) as exc:
                activate_permit_sync(
                    db,
                    job_id=job.id,
                    host_id=host.id,
                    payload=ActivatePermitIn(
                        request_id="req-c5-lease",
                        client_boot_id=host.boot_id or "",
                    ),
                )
            assert exc.value.code == "no_active_lease"

    def test_activate_and_session_watchdog_no_deadlock(self, db_session):
        """Real ``_reconcile_host_heartbeat_timeouts`` × ``activate_permit`` lock order.

        Watchdog advances its scan clock so the host is selected (Host FOR UPDATE
        → Job FOR UPDATE) while activate still sees a fresh heartbeat wall clock
        (Host FOR SHARE → Job → PlanRun). Same Host→Job order; must not 40P01.
        """
        job, _run, host, _dev = _seed_running_atomic_job(db_session)
        job_id, host_id, boot_id = job.id, host.id, host.boot_id or ""
        with SessionLocal() as db:
            pre_register_atomic_commit_sync(
                db,
                job_id=job_id,
                payload=PreRegisterIn(
                    request_id="req-c5-race",
                    min_critical_section_seconds=1,
                ),
            )
            h = db.get(Host, host_id)
            h.last_heartbeat = _fresh_now()
            db.commit()

        outcomes: dict[str, object] = {}

        def do_activate():
            with SessionLocal() as db:
                _set_timeouts(db)
                try:
                    out = activate_permit_sync(
                        db,
                        job_id=job_id,
                        host_id=host_id,
                        payload=ActivatePermitIn(
                            request_id="req-c5-race",
                            client_boot_id=boot_id,
                        ),
                    )
                    db.commit()
                    outcomes["activate"] = out["status"]
                except Exception as exc:  # lock_timeout / FinishingGuardError
                    db.rollback()
                    outcomes["activate"] = f"{type(exc).__name__}:{getattr(exc, 'code', exc)}"

        def do_watchdog():
            async def _run():
                await async_engine.dispose()
                async with AsyncSessionLocal() as db:
                    await db.execute(text("SET LOCAL deadlock_timeout = '200ms'"))
                    await db.execute(text("SET LOCAL lock_timeout = '5s'"))
                    # Future scan clock → host appears expired to watchdog only.
                    scan_now = _fresh_now() + timedelta(hours=2)
                    await _reconcile_host_heartbeat_timeouts(db, now=scan_now)
                    await db.commit()
                    outcomes["watchdog"] = "ok"

            try:
                asyncio.run(_run())
            except Exception as exc:
                outcomes["watchdog"] = f"{type(exc).__name__}:{exc}"

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(do_activate)
            f2 = pool.submit(do_watchdog)
            try:
                f1.result(timeout=20)
                f2.result(timeout=20)
            except FuturesTimeout as exc:
                raise AssertionError("C5 concurrent path hung (possible deadlock)") from exc
        assert "activate" in outcomes
        assert outcomes.get("watchdog") == "ok"
        assert "40P01" not in str(outcomes)
        assert "deadlock" not in str(outcomes).lower()


class TestC6CloseAdmission:
    def test_close_then_drain_zero_blocks_pre_register(self, db_session):
        job, _run, _host, _dev = _seed_running_atomic_job(db_session)
        with SessionLocal() as db:
            close_admission_sync(db)
            db.commit()
        with SessionLocal() as db:
            assert db.get(FinishingGuardAdmission, 1).admission_open is False
            assert count_active_atomic_guards_sync(db) == 0
            with pytest.raises(FinishingGuardError) as exc:
                pre_register_atomic_commit_sync(
                    db,
                    job_id=job.id,
                    payload=PreRegisterIn(
                        request_id="req-c6-late",
                        min_critical_section_seconds=1,
                    ),
                )
            assert exc.value.code == "admission_closed"

    def test_close_and_pre_register_race_no_deadlock(self, db_session):
        job, _run, _host, _dev = _seed_running_atomic_job(db_session)
        job_id = job.id
        outcomes: dict[str, object] = {}

        def do_close():
            with SessionLocal() as db:
                _set_timeouts(db)
                close_admission_sync(db)
                db.commit()
                outcomes["close"] = "ok"

        def do_pre():
            with SessionLocal() as db:
                _set_timeouts(db)
                try:
                    pre_register_atomic_commit_sync(
                        db,
                        job_id=job_id,
                        payload=PreRegisterIn(
                            request_id="req-c6-race",
                            min_critical_section_seconds=1,
                        ),
                    )
                    db.commit()
                    outcomes["pre"] = "ok"
                except FinishingGuardError as exc:
                    db.rollback()
                    outcomes["pre"] = exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(do_close)
            f2 = pool.submit(do_pre)
            try:
                f1.result(timeout=20)
                f2.result(timeout=20)
            except FuturesTimeout as exc:
                raise AssertionError("C6 concurrent path hung (possible deadlock)") from exc
        assert outcomes.get("close") == "ok"
        assert outcomes.get("pre") in {"ok", "admission_closed"}
        with SessionLocal() as db:
            if count_active_atomic_guards_sync(db) == 0:
                with pytest.raises(FinishingGuardError) as exc:
                    pre_register_atomic_commit_sync(
                        db,
                        job_id=job_id,
                        payload=PreRegisterIn(
                            request_id="req-c6-after",
                            min_critical_section_seconds=1,
                        ),
                    )
                assert exc.value.code == "admission_closed"


def _complete_payload(*, fencing_token: str, status: str = "FINISHED", exit_code: int = 0) -> _RunCompleteIn:
    return _RunCompleteIn(
        update={"status": status, "exit_code": exit_code},
        fencing_token=fencing_token,
    )


def _run_complete_job(job_id: int, payload: _RunCompleteIn) -> dict:
    """Sync wrapper around async complete with side-effect patches."""

    async def _run():
        await async_engine.dispose()
        with patch(
            "backend.services.agent_completion.broadcast_run_job_update",
            new=AsyncMock(),
        ), patch(
            "backend.core.task_queue.get_queue",
        ) as get_queue:
            get_queue.return_value.enqueue = AsyncMock()
            async with AsyncSessionLocal() as db:
                await db.execute(text("SET LOCAL deadlock_timeout = '200ms'"))
                await db.execute(text("SET LOCAL lock_timeout = '5s'"))
                return await complete_agent_job(db, job_id, payload)

    return asyncio.run(_run())


class TestC7ForceAbortComplete:
    def test_force_abort_on_terminal_and_unsupported_host(self, db_session):
        job, run, _host, _dev = _seed_running_atomic_job(db_session)
        with SessionLocal() as db:
            j = db.get(JobInstance, job.id)
            j.status = JobStatus.COMPLETED.value
            j.ended_at = _fresh_now()
            fact = {
                "update": {"status": "COMPLETED"},
                "artifact": None,
                "watcher_summary": None,
                "disarm_results": None,
            }
            j.terminal_payload_digest = hashlib.sha256(
                json.dumps(
                    fact, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            db.commit()
        with SessionLocal() as db:
            summary = abort_plan_run(
                run.id, db=db, reason="force-after-complete", force=True,
            )
            db.commit()
        assert summary.already_terminal or summary.force_requested_before_complete_settled

        _job2, run2, host2, _ = _seed_running_atomic_job(db_session)
        with SessionLocal() as db:
            h = db.get(Host, host2.id)
            h.agent_capabilities = [FINISHING_GUARD_V2, ATOMIC_COMMIT]
            db.commit()
        with SessionLocal() as db:
            summary2 = abort_plan_run(
                run2.id, db=db, reason="force-no-cap", force=True,
            )
            db.commit()
        assert host2.id in summary2.force_unsupported_hosts

    def test_force_abort_includes_unknown_candidate(self, db_session):
        job, run, _host, _dev = _seed_running_atomic_job(db_session, unknown=True)
        with SessionLocal() as db:
            summary = abort_plan_run(
                run.id, db=db, reason="force-unknown", force=True,
            )
            db.commit()
        assert job.id in summary.abort_requested_jobs

    def test_force_abort_and_complete_interleave_no_deadlock(self, db_session):
        """C7: concurrent ``abort(force=true)`` × ``/complete`` — no 40P01; settle terminal."""
        job, run, _host, device = _seed_running_atomic_job(db_session)
        job_id, run_id = job.id, run.id
        fencing = f"{device.id}:1"
        accepted = _complete_payload(fencing_token=fencing)
        outcomes: dict[str, object] = {}

        def do_abort():
            with SessionLocal() as db:
                _set_timeouts(db)
                try:
                    with patch(
                        "backend.services.plan_run_abort.schedule_emit",
                    ), patch(
                        "backend.services.plan_run_abort.should_trigger_dedup",
                        return_value=False,
                    ):
                        summary = abort_plan_run(
                            run_id, db=db, reason="force-race", force=True,
                        )
                        db.commit()
                    outcomes["abort"] = {
                        "ok": True,
                        "already_terminal": summary.already_terminal,
                        "force_before_settle": (
                            summary.force_requested_before_complete_settled
                        ),
                        "abort_requested": list(summary.abort_requested_jobs),
                    }
                except PlanRunAbortError as exc:
                    db.rollback()
                    # Complete may have terminalized the PlanRun first — still a
                    # settled race, not a lock failure.
                    outcomes["abort"] = {
                        "ok": True,
                        "plan_run_already_terminal": True,
                        "error": str(exc),
                    }
                except Exception as exc:
                    db.rollback()
                    outcomes["abort"] = f"{type(exc).__name__}:{exc}"

        def do_complete():
            try:
                result = _run_complete_job(job_id, accepted)
                outcomes["complete"] = result
            except Conflict as exc:
                outcomes["complete"] = {
                    "conflict": True,
                    "detail": getattr(exc, "detail", str(exc)),
                }
            except Exception as exc:
                outcomes["complete"] = f"{type(exc).__name__}:{exc}"

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(do_abort)
            f2 = pool.submit(do_complete)
            try:
                f1.result(timeout=25)
                f2.result(timeout=25)
            except FuturesTimeout as exc:
                raise AssertionError("C7 concurrent path hung (possible deadlock)") from exc

        assert "abort" in outcomes and "complete" in outcomes
        assert "40P01" not in str(outcomes)
        assert "deadlock" not in str(outcomes).lower()
        assert isinstance(outcomes["abort"], dict) and outcomes["abort"].get("ok") is True
        # Complete either committed a terminal fact or lost a conflict to abort settle.
        assert isinstance(outcomes["complete"], dict)

        with SessionLocal() as db:
            settled = db.get(JobInstance, job_id)
            assert settled is not None
            assert settled.status in {
                JobStatus.COMPLETED.value,
                JobStatus.ABORTED.value,
                JobStatus.FAILED.value,
                JobStatus.RUNNING.value,  # force may only mark abort_requested
            }
            token = fencing
            digest = settled.terminal_payload_digest

        # Idempotent replay + stale/conflict probes when complete left a digest.
        if digest:
            replay = _run_complete_job(job_id, accepted)
            assert replay.get("idempotent") is True or replay.get("status") in {
                JobStatus.COMPLETED.value,
                JobStatus.ABORTED.value,
                JobStatus.FAILED.value,
            }

            # Wrong fencing on terminal → stale_job_completion_rejected / Conflict.
            with pytest.raises(Conflict) as stale_exc:
                _run_complete_job(
                    job_id,
                    _complete_payload(fencing_token="wrong-token:0"),
                )
            assert stale_exc.value.detail["code"] == "STALE_COMPLETION_TOKEN"

            # Divergent terminal payload → terminal_payload_conflict.
            with pytest.raises(Conflict) as conflict_exc:
                _run_complete_job(
                    job_id,
                    _RunCompleteIn(
                        update={"status": "FINISHED", "exit_code": 99},
                        fencing_token=token,
                    ),
                )
            assert conflict_exc.value.detail["code"] == "TERMINAL_PAYLOAD_CONFLICT"
        else:
            # Force won before complete settled: abort_requested must cover job,
            # or complete lost with a Conflict detail.
            abort_meta = outcomes["abort"]
            complete_meta = outcomes["complete"]
            assert (
                job_id in abort_meta.get("abort_requested", [])
                or abort_meta.get("force_before_settle")
                or abort_meta.get("already_terminal")
                or abort_meta.get("plan_run_already_terminal")
                or complete_meta.get("conflict") is True
            )


class TestC8FlashIntegrityDebt:
    def test_closing_flash_only_keeps_b6_debt(self, db_session):
        job, _run, _host, device = _seed_running_atomic_job(db_session)
        now = _fresh_now()
        dr._upsert_open_debt_sync(
            db_session,
            device_id=device.id,
            family="power-cycle",
            source_kind="job",
            source_job_id=job.id,
            source_fencing_token="t0",
            cause="test",
            evidence={},
            now=now,
        )
        dr._upsert_open_debt_sync(
            db_session,
            device_id=device.id,
            family="flash_integrity",
            source_kind="job",
            source_job_id=job.id,
            source_fencing_token="t0",
            cause="atomic_pre_register",
            evidence={},
            now=now,
        )
        db_session.commit()
        with SessionLocal() as db:
            dr.close_flash_integrity_debts_sync(db, device_id=device.id, now=now)
            db.commit()
        open_rows = db_session.execute(
            select(DeviceReturnDebt.family).where(
                DeviceReturnDebt.device_id == device.id,
                DeviceReturnDebt.state == "open",
            )
        ).scalars().all()
        assert open_rows == ["power-cycle"]
