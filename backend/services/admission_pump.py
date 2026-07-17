"""ADR-0026 P1 Step 4 — Admission queue pump + admission transaction.

Ownership chain (single owner at every stage, reviewer boundary):

    QUEUED ──pump tick (FOR UPDATE SKIP LOCKED, atomic claim)──▶ PRECHECK
    PRECHECK ──SAQ plan_admission_task──▶
        Phase A (NO locks): slow ops — script sha256 verify via Agent RPC,
                            hot-update sync on mismatch, host reachability
        Phase B (ONE short tx): lock PlanRun + all PlanRunHost rows in stable
                            order → re-verify ALL target devices → WiFi
                            allocation → bulk Job insert → counters →
                            PRECHECK→RUNNING → commit
    Any retryable competition (device busy/offline, pool full, unique-index
    race) → full rollback → fresh tx writes back QUEUED + queue_reason +
    next_admission_at (enqueued_at preserved) — never FAILED (invariant ④).
    Only non-retryable errors (device deleted / no host / script contract
    failure after sync retry) settle FAILED.

Drain semantics (reviewer boundary #5): the pump admits existing QUEUED runs
regardless of the env flag — turning the flag off stops NEW V2 runs at
prepare (admission_queue_enabled gates creation) while the pump keeps
draining until the queue is empty. Forced stop = explicit per-run abort
(QUEUED abort → FAILED with audit, step 2), never silent.

Multi-host semantics: one PlanRun admits as a unit — all hosts' devices are
re-verified and all jobs materialize in the same transaction; there is no
partial per-host admission (no implicit rolling).
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.core.audit import record_audit
from backend.core.database import SessionLocal
from backend.models.enums import PlanRunStatus
from backend.models.plan_run import PlanRun, PlanRunHost, PlanRunTargetDevice
from backend.services.plan_dispatcher_core import (
    PlanDispatchError,
    build_lifecycle_from_snapshot as _build_lifecycle_from_snapshot,
)
from backend.services.plan_dispatcher_sync import (
    AllocationError,
    _FATAL_DISPATCH_REASONS,
    _classify_dispatch_devices_sync,
    materialize_jobs_and_allocations,
)
from backend.services.state_machine import PlanRunStateMachine

logger = logging.getLogger(__name__)

ADMISSION_PUMP_BATCH = int(os.getenv("STP_ADMISSION_PUMP_BATCH", "5"))
ADMISSION_RETRY_BACKOFF_SECONDS = int(os.getenv("STP_ADMISSION_RETRY_BACKOFF_SECONDS", "30"))
# Anti-starvation aging: every AGING_STEP seconds of queue wait adds one
# effective priority point, capped at AGING_MAX_BOOST (0 disables).
ADMISSION_AGING_STEP_SECONDS = int(os.getenv("STP_ADMISSION_AGING_STEP_SECONDS", "1800"))
ADMISSION_AGING_MAX_BOOST = int(os.getenv("STP_ADMISSION_AGING_MAX_BOOST", "5"))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ── Pump tick: claim QUEUED → PRECHECK ────────────────────────────────────────


def claim_queued_plan_runs(db: Session, *, batch: int | None = None) -> list[tuple[int, str]]:
    """Atomically claim up to *batch* QUEUED runs for admission.

    Scan order = effective priority (priority + aging boost) DESC, then FIFO
    by enqueued_at. FOR UPDATE SKIP LOCKED keeps overlapping ticks from
    fighting over the same rows. Each claimed run flips to PRECHECK with a
    fresh admission_attempt_id in the SAME transaction — after commit the
    pump owns it and the stale-PRECHECK reaper (step 2) is the safety net.

    Returns [(plan_run_id, admission_attempt_id), ...].
    """
    now = _utc_now()
    limit = batch or ADMISSION_PUMP_BATCH

    if ADMISSION_AGING_STEP_SECONDS > 0 and ADMISSION_AGING_MAX_BOOST > 0:
        aging_boost = text(
            "LEAST(FLOOR(EXTRACT(EPOCH FROM (now() - enqueued_at)) / "
            f"{ADMISSION_AGING_STEP_SECONDS}), {ADMISSION_AGING_MAX_BOOST})"
        )
        order = [
            (PlanRun.priority + aging_boost).desc(),
            PlanRun.enqueued_at.asc(),
            PlanRun.id.asc(),
        ]
    else:
        order = [PlanRun.priority.desc(), PlanRun.enqueued_at.asc(), PlanRun.id.asc()]

    candidates = db.execute(
        select(PlanRun)
        .where(
            PlanRun.status == PlanRunStatus.QUEUED.value,
            or_(
                PlanRun.next_admission_at.is_(None),
                PlanRun.next_admission_at <= now,
            ),
        )
        .order_by(*order)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).scalars().all()

    claimed: list[tuple[int, str]] = []
    for pr in candidates:
        attempt_id = uuid.uuid4().hex
        PlanRunStateMachine.transition(pr, PlanRunStatus.PRECHECK, reason="pump_claim")
        pr.precheck_started_at = now
        pr.admission_attempt_id = attempt_id
        if pr.admission_token is None:
            pr.admission_token = uuid.uuid4().hex
        claimed.append((pr.id, attempt_id))
    if claimed:
        db.commit()
    else:
        db.rollback()
    return claimed


def pump_admission_tick() -> dict[str, int]:
    """APScheduler callback: claim a batch and hand each run to the SAQ
    admission task.

    required=True makes enqueue block until Redis CONFIRMS the job (pump runs
    in an APScheduler worker thread, so the sync bridge is safe) — best-effort
    mode would report success before the actual enqueue and strand the run in
    PRECHECK until the reaper (reviewer, Step 4.1). Confirmed failure requeues
    immediately.

    When the SAQ producer is not ready (worker not started, e.g.
    STP_ENABLE_INPROCESS_SAQ=0), the tick short-circuits WITHOUT claiming —
    claiming without an executor would just churn QUEUED↔PRECHECK.
    """
    from backend.tasks.saq_worker import enqueue_sync, is_saq_ready

    summary = {"claimed": 0, "enqueued": 0, "requeued_on_enqueue_failure": 0}
    if not is_saq_ready():
        logger.debug("admission_pump_skip_saq_not_ready")
        return summary

    with SessionLocal() as db:
        claimed = claim_queued_plan_runs(db)
        summary["claimed"] = len(claimed)

        for run_id, attempt_id in claimed:
            ok = False
            try:
                ok = enqueue_sync(
                    "plan_admission_task",
                    key=f"admission:{run_id}:{attempt_id}",
                    timeout=600,
                    retries=1,
                    required=True,  # Redis-confirmed or raises — never fire-and-forget
                    plan_run_id=run_id,
                    attempt_id=attempt_id,
                )
            except Exception:
                logger.exception("admission_enqueue_failed plan_run=%d", run_id)
            if ok:
                summary["enqueued"] += 1
            else:
                requeue_plan_run(
                    db, run_id, attempt_id,
                    queue_reason="PRECHECK_STALE",
                    blockers=[{"reason": "admission_enqueue_failed"}],
                    backoff_seconds=ADMISSION_RETRY_BACKOFF_SECONDS,
                )
                summary["requeued_on_enqueue_failure"] += 1
    if summary["claimed"]:
        logger.info(
            "admission_pump_tick claimed=%d enqueued=%d requeue=%d",
            summary["claimed"], summary["enqueued"],
            summary["requeued_on_enqueue_failure"],
        )
    return summary


# ── Requeue / fail helpers (fresh short transactions) ─────────────────────────


def requeue_plan_run(
    db: Session,
    run_id: int,
    attempt_id: str,
    *,
    queue_reason: str,
    blockers: list[dict] | None = None,
    backoff_seconds: int | None = None,
) -> bool:
    """Competition write-back: PRECHECK → QUEUED (invariant ④).

    Ownership CAS: only the holder of *attempt_id* may requeue — a reaper
    that already recovered the run (rotating the attempt) wins. enqueued_at
    is intentionally preserved (aging basis). Clean competition requeues do
    NOT consume admission_requeue_attempts — that counter belongs to the
    stale-PRECHECK reaper (crash recovery), not to normal queue waiting.
    """
    now = _utc_now()
    pr = db.execute(
        select(PlanRun).where(PlanRun.id == run_id).with_for_update()
    ).scalar_one_or_none()
    if pr is None:
        db.rollback()
        return False
    if (
        pr.status != PlanRunStatus.PRECHECK.value
        or pr.admission_attempt_id != attempt_id
    ):
        db.rollback()
        logger.info(
            "admission_requeue_skip_not_owner plan_run=%d status=%s", run_id, pr.status,
        )
        return False

    PlanRunStateMachine.transition(pr, PlanRunStatus.QUEUED, reason=f"requeue:{queue_reason}")
    pr.queue_reason = queue_reason
    pr.next_admission_at = now + timedelta(
        seconds=backoff_seconds if backoff_seconds is not None else ADMISSION_RETRY_BACKOFF_SECONDS
    )
    pr.admission_attempt_id = None
    pr.precheck_started_at = None
    run_ctx = dict(pr.run_context or {})
    if blockers is not None:
        run_ctx["queue_blockers"] = blockers
        pr.run_context = run_ctx
        flag_modified(pr, "run_context")
    db.commit()
    logger.info(
        "admission_requeued plan_run=%d reason=%s next=%s",
        run_id, queue_reason, pr.next_admission_at.isoformat(),
    )
    return True


def fail_plan_run_admission(
    db: Session,
    run_id: int,
    attempt_id: str,
    *,
    reason: str,
    detail: dict | None = None,
) -> bool:
    """Non-retryable admission failure: PRECHECK → FAILED (config/contract
    errors only — never use for competition, see requeue_plan_run)."""
    now = _utc_now()
    pr = db.execute(
        select(PlanRun).where(PlanRun.id == run_id).with_for_update()
    ).scalar_one_or_none()
    if pr is None:
        db.rollback()
        return False
    if (
        pr.status != PlanRunStatus.PRECHECK.value
        or pr.admission_attempt_id != attempt_id
    ):
        db.rollback()
        return False

    PlanRunStateMachine.transition(pr, PlanRunStatus.FAILED, reason=reason)
    pr.ended_at = now
    pr.result_summary = {
        "dispatch_failed": True,
        "reason": reason,
        **(detail or {}),
    }
    flag_modified(pr, "result_summary")
    record_audit(
        db,
        action="plan_admission_failed",
        resource_type="plan_run",
        resource_id=run_id,
        details={"reason": reason, **(detail or {})},
        username="system",
    )
    db.commit()
    logger.warning("admission_failed plan_run=%d reason=%s", run_id, reason)
    return True


# ── Phase A: slow prechecks (no locks) ────────────────────────────────────────


class _RetryableAdmission(Exception):
    def __init__(self, queue_reason: str, blockers: list[dict]):
        self.queue_reason = queue_reason
        self.blockers = blockers
        super().__init__(queue_reason)


class _FatalAdmission(Exception):
    def __init__(self, reason: str, detail: dict | None = None):
        self.reason = reason
        self.detail = detail or {}
        super().__init__(reason)


# push_mismatched_scripts error codes that are DETERMINISTIC config faults
# (retrying can never help) rather than transient SSH/network errors. Matched
# against the returned error string's prefix. Everything else (ssh_exception,
# ssh_timeout, SFTP failures, …) is treated as transient → requeue.
_FATAL_PUSH_ERROR_PREFIXES = (
    "host_not_found",
    "host_missing_ip",
    "no_ssh_credentials",
    "ssh_security_config_error",
    "cannot map nfs_path",
    "local file not found",
)


def _is_fatal_push_error(err: Optional[str]) -> bool:
    return bool(err) and err.startswith(_FATAL_PUSH_ERROR_PREFIXES)


async def _verify_scripts_phase(run_id: int, host_ids: list[str]) -> None:
    """Script sha256 verify (+ one hot-update sync retry on mismatch).

    Agent unreachable → retryable (host may reconnect). Content mismatch
    after a sync attempt → fatal (script contract, invariant ④).
    """
    import asyncio

    from backend.services.precheck.scripts import expected_scripts_for_run
    from backend.services.precheck.sync import push_mismatched_scripts
    from backend.services.precheck.verify import gather_verify

    def _load_expected() -> list[dict]:
        with SessionLocal() as db:
            pr = db.get(PlanRun, run_id)
            if pr is None:
                raise _FatalAdmission("plan_run_gone")
            return expected_scripts_for_run(pr, db)

    expected = await asyncio.to_thread(_load_expected)
    if not expected:
        raise _FatalAdmission("no_expected_scripts")

    results = await gather_verify(host_ids, expected)

    def _is_unreachable(err: Optional[str]) -> bool:
        return err == "agent_offline" or (err or "").startswith(
            ("rpc_failed", "verify_exception")
        )

    unreachable = [
        {"host_id": hid, "reason": "host_unreachable", "error": err}
        for hid, (ok, _res, err) in results.items()
        if not ok and _is_unreachable(err)
    ]
    mismatched_hosts = {
        hid: [r for r in res if not r.get("ok")]
        for hid, (ok, res, err) in results.items()
        if not ok and err == "sha_mismatch"
    }

    if mismatched_hosts:
        # One sync-and-reverify round, mirroring the legacy gate's healing.
        def _push_all() -> dict[str, tuple[bool, Optional[str]]]:
            out: dict[str, tuple[bool, Optional[str]]] = {}
            with SessionLocal() as db:
                for hid, bad in mismatched_hosts.items():
                    out[hid] = push_mismatched_scripts(hid, bad, db)
            return out

        push_results = await asyncio.to_thread(_push_all)
        # Split push failures (Step 4.1 hardening review): deterministic
        # config faults are fatal — no SSH creds / missing local script /
        # host gone cannot heal by requeueing. Transient SSH/network faults
        # requeue as before.
        push_fatal = [
            {"host_id": hid, "reason": "script_sync_config_error", "error": err}
            for hid, (ok, err) in push_results.items()
            if not ok and _is_fatal_push_error(err)
        ]
        push_transient = [
            {"host_id": hid, "reason": "script_sync_failed", "error": err}
            for hid, (ok, err) in push_results.items()
            if not ok and not _is_fatal_push_error(err)
        ]
        retry_hosts = [hid for hid, (ok, _err) in push_results.items() if ok]
        still_mismatched: list[str] = []
        if retry_hosts:
            reverify = await gather_verify(retry_hosts, expected)
            for hid, (ok, _res, err) in reverify.items():
                if ok:
                    mismatched_hosts.pop(hid, None)
                elif _is_unreachable(err):
                    # Agent went offline between push and reverify — transient,
                    # NOT a contract failure. Requeue instead of FAILED.
                    unreachable.append(
                        {"host_id": hid, "reason": "host_unreachable", "error": err}
                    )
                    mismatched_hosts.pop(hid, None)
                else:
                    still_mismatched.append(hid)

        # Deterministic config error fails fast — requeueing cannot fix it.
        if push_fatal:
            raise _FatalAdmission("script_sync_config_error", {"hosts": push_fatal})

        if push_transient:
            # Retryable infra faults take priority over a fatal mismatch
            # verdict — the sync never actually completed on these hosts.
            raise _RetryableAdmission("DEVICE_BUSY", unreachable + push_transient)

        # FATAL only when the sync SUCCEEDED and reverify still reports a real
        # sha mismatch (a genuine script contract failure).
        confirmed_mismatch = [h for h in still_mismatched if h in mismatched_hosts]
        if confirmed_mismatch:
            raise _FatalAdmission(
                "script_verify_failed",
                {"hosts": sorted(confirmed_mismatch)},
            )

    if unreachable:
        raise _RetryableAdmission("DEVICE_BUSY", unreachable)


# ── Phase B: short admission transaction ──────────────────────────────────────


def admission_transaction(db: Session, run_id: int, attempt_id: str) -> bool:
    """The single short transaction that admits a PlanRun (all hosts as one
    unit). Returns True when the run reached RUNNING.

    Lock order (deadlock-safe across concurrent admissions):
      1. PlanRun row FOR UPDATE (ownership CAS on attempt_id)
      2. all PlanRunHost rows FOR UPDATE, ORDER BY host_id
      3. target devices re-verified WITHOUT device-row locks — the
         uq_job_active_per_device index is the admission arbiter (ADR §4).
    """
    pr = db.execute(
        select(PlanRun).where(PlanRun.id == run_id).with_for_update()
    ).scalar_one_or_none()
    if pr is None:
        db.rollback()
        return False
    if (
        pr.status != PlanRunStatus.PRECHECK.value
        or pr.admission_attempt_id != attempt_id
    ):
        # Reaper recovered it / abort landed — we are no longer the owner.
        db.rollback()
        logger.info(
            "admission_tx_skip_not_owner plan_run=%d status=%s", run_id, pr.status,
        )
        return False

    host_rows = db.execute(
        select(PlanRunHost)
        .where(PlanRunHost.plan_run_id == run_id)
        .order_by(PlanRunHost.host_id)
        .with_for_update()
    ).scalars().all()

    targets = db.execute(
        select(PlanRunTargetDevice)
        .where(PlanRunTargetDevice.plan_run_id == run_id)
        .order_by(PlanRunTargetDevice.device_id)
    ).scalars().all()
    if not targets:
        db.rollback()
        raise _FatalAdmission("snapshot_missing_targets")

    device_ids = [t.device_id for t in targets]

    # Final re-verification of EVERY target device (all-or-nothing admission).
    unavailable, device_host_map = _classify_dispatch_devices_sync(db, device_ids)
    fatal = [e for e in unavailable if e["reason"] in _FATAL_DISPATCH_REASONS]
    if fatal:
        db.rollback()
        raise _FatalAdmission(
            "devices_unavailable_at_admission", {"unavailable_devices": fatal},
        )
    if unavailable:
        db.rollback()
        raise _RetryableAdmission("DEVICE_BUSY", unavailable)

    # Immutable-snapshot integrity (reviewer, Step 4.1): the run was verified
    # and grouped against host_id_snapshot at prepare; a device that migrated
    # hosts while queued would otherwise be materialized on a host with no
    # PlanRunHost projection and no script verification. Drift is explicit,
    # non-retryable configuration divergence — never silently admit on the
    # current host. (target.host_id_snapshot == PlanRunHost.host_id holds by
    # construction: targets are FK-bound to their host-group row.)
    drifted = [
        {
            "id": t.device_id,
            "reason": "host_drift",
            "host_id_snapshot": t.host_id_snapshot,
            "current_host_id": device_host_map.get(t.device_id),
        }
        for t in targets
        if device_host_map.get(t.device_id) != t.host_id_snapshot
    ]
    if drifted:
        db.rollback()
        raise _FatalAdmission("device_host_drift", {"drifted_devices": drifted})

    now = _utc_now()
    lifecycle = _build_lifecycle_from_snapshot(pr.plan_snapshot)
    try:
        materialize_jobs_and_allocations(db, pr, lifecycle, device_ids, device_host_map)
    except AllocationError as exc:
        db.rollback()
        raise _RetryableAdmission(
            "RESOURCE_BUSY", [{"reason": "wifi_pool_full", "error": str(exc)}],
        )
    except IntegrityError as exc:
        if "uq_job_active_per_device" not in str(exc.orig or exc):
            raise
        db.rollback()
        raise _RetryableAdmission(
            "DEVICE_BUSY", [{"reason": "device_claimed_concurrently"}],
        )

    # Counters (O(1) aggregation basis) + per-host admission bookkeeping.
    per_host = {h.host_id: 0 for h in host_rows}
    for did in device_ids:
        per_host[device_host_map[did]] = per_host.get(device_host_map[did], 0) + 1
    pr.total_job_count = len(device_ids)
    for h in host_rows:
        h.status = "ADMITTED"
        h.admitted_at = now
        h.total_job_count = per_host.get(h.host_id, 0)

    PlanRunStateMachine.transition(pr, PlanRunStatus.RUNNING, reason="admitted")
    # Real execution start — queue wait is measured from enqueued_at only.
    pr.started_at = now
    pr.queue_reason = None
    pr.next_admission_at = None
    pr.precheck_started_at = None
    db.commit()
    logger.info(
        "plan_run_admitted plan_run=%d jobs=%d hosts=%d",
        run_id, len(device_ids), len(host_rows),
    )
    return True


# ── SAQ task: Phase A + Phase B + outcome routing ─────────────────────────────


async def plan_admission_task(ctx: dict, *, plan_run_id: int, attempt_id: str) -> None:
    """SAQ task owning one admission attempt end-to-end."""
    import asyncio

    def _host_ids() -> list[str]:
        with SessionLocal() as db:
            pr = db.get(PlanRun, plan_run_id)
            if (
                pr is None
                or pr.status != PlanRunStatus.PRECHECK.value
                or pr.admission_attempt_id != attempt_id
            ):
                return []
            return [
                row[0] for row in db.execute(
                    select(PlanRunHost.host_id)
                    .where(PlanRunHost.plan_run_id == plan_run_id)
                    .order_by(PlanRunHost.host_id)
                ).all()
            ]

    def _requeue(reason: str, blockers: list[dict]) -> None:
        with SessionLocal() as db:
            requeue_plan_run(db, plan_run_id, attempt_id, queue_reason=reason, blockers=blockers)

    def _fail(reason: str, detail: dict) -> None:
        with SessionLocal() as db:
            fail_plan_run_admission(db, plan_run_id, attempt_id, reason=reason, detail=detail)

    try:
        host_ids = await asyncio.to_thread(_host_ids)
        if not host_ids:
            logger.info("admission_task_skip_not_owner plan_run=%d", plan_run_id)
            return

        # Phase A — slow ops, zero DB locks held.
        await _verify_scripts_phase(plan_run_id, host_ids)

        # Phase B — the short admission transaction.
        def _admit() -> bool:
            with SessionLocal() as db:
                return admission_transaction(db, plan_run_id, attempt_id)

        await asyncio.to_thread(_admit)
    except _RetryableAdmission as exc:
        await asyncio.to_thread(_requeue, exc.queue_reason, exc.blockers)
    except _FatalAdmission as exc:
        await asyncio.to_thread(_fail, exc.reason, exc.detail)
    except Exception:
        # Unknown error: leave PRECHECK in place — the stale-PRECHECK reaper
        # (step 2) recovers it to QUEUED with attempt bookkeeping. Re-raise so
        # SAQ records the failure.
        logger.exception("admission_task_error plan_run=%d", plan_run_id)
        raise
