"""Agent 批量租约续租（#1520 垂直切片：agent_api extend-batch）。

``POST /leases/extend-batch``：预检分类 → Job→Lease 锁序 CAS → execution_state /
progress 回写。路由退化为 ``ok(await extend_agent_leases_batch(...))``。

``VALID_EXECUTION_STATES`` / ``parse_progress_ts`` 亦供 coordinator_heartbeat
经路由 re-export 使用。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel
from sqlalchemy import bindparam, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.metrics import record_lease_extend_batch
from backend.models.device_lease import DeviceLease
from backend.models.enums import JobStatus, LeaseStatus, LeaseType
from backend.models.job import JobInstance
from backend.services.errors import BatchTooLarge

_DEVICE_LOCK_LEASE_SECONDS = int(os.getenv("DEVICE_LOCK_LEASE_SECONDS", "600"))


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


_LEASE_EXTEND_BATCH_MAX = int(os.getenv("AGENT_LEASE_EXTEND_BATCH_MAX", "200"))

# ADR-0026 §3 execution_state sub-states (invariant ③). Anything else reported
# by an Agent is ignored (column left untouched) — forward/backward tolerant.
_VALID_EXECUTION_STATES = {
    "WAITING_EXECUTION_SLOT",
    "EXECUTING_STEP",
    "PATROL_SLEEP",
    "WAITING_BARRIER",
}


def _parse_progress_ts(raw: Any) -> Optional[datetime]:
    """Parse progress_marker.last_progress_at (ISO8601) → aware UTC datetime."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(ts)


class _ExtendBatchItemIn(BaseModel):
    job_id: int
    fencing_token: str
    # ADR-0026 invariant ③: execution_state + progress_marker ride on the same
    # renewal request and are persisted on successful CAS (see extend_leases_batch).
    # progress_marker is a structured snapshot (e.g. {"patrol_cycle_index": N,
    # "last_progress_at": "..."}), so it is typed as an open dict, not a string.
    execution_state: Optional[str] = None
    progress_marker: Optional[Dict[str, Any]] = None


class _ExtendBatchIn(BaseModel):
    host_id: str
    agent_instance_id: str = ""
    leases: List[_ExtendBatchItemIn]


class _ExtendBatchItemOut(BaseModel):
    job_id: int
    # renewed | stale_token | job_not_running | lease_missing
    status: str
    expires_at: Optional[str] = None


class _ExtendBatchOut(BaseModel):
    results: List[_ExtendBatchItemOut]


async def _cas_renew_leases(
    db: AsyncSession,
    *,
    pairs: List[tuple],
    host_id: str,
    agent_instance_id: str,
    now: datetime,
    new_expires: datetime,
) -> set[int]:
    """Final ownership CAS for batch renewal. Returns job_ids actually renewed.

    The caller's prelim classification validated a SNAPSHOT; between that
    SELECT and this UPDATE the lease can be released and re-acquired (token
    rotated, e.g. recovery takeover). Matching by job_id alone would let the
    OLD Agent renew the NEW owner's lease. So this UPDATE re-asserts the full
    ownership tuple at write time:
      - (job_id, fencing_token) pair-bound via row-value IN
      - host binding; agent-instance binding when the Agent reports one
      - ACTIVE + expires_at > now (never revive a grace-held lease)
      - Job.status == RUNNING join (a job that went terminal concurrently
        must not get a fresh TTL)
    Does NOT commit — runs inside the caller's transaction.

    Caller must already hold ``JobInstance`` row locks for the target jobs
    (``FOR UPDATE``, ordered by id) before invoking this (#992): complete
    locks Job then Lease; batch renew must match that order.
    """
    conditions = [
        tuple_(DeviceLease.job_id, DeviceLease.fencing_token).in_(pairs),
        DeviceLease.lease_type == LeaseType.JOB.value,
        DeviceLease.status == LeaseStatus.ACTIVE.value,
        DeviceLease.expires_at > now,  # Phase 4b: refuse expired lease
        DeviceLease.host_id == host_id,
        JobInstance.id == DeviceLease.job_id,  # UPDATE .. FROM job_instance
        JobInstance.status == JobStatus.RUNNING.value,
    ]
    if agent_instance_id:
        conditions.append(DeviceLease.agent_instance_id == agent_instance_id)
    renewed_rows = (await db.execute(
        update(DeviceLease)
        .where(*conditions)
        .values(renewed_at=now, expires_at=new_expires)
        .returning(DeviceLease.job_id)
        .execution_options(synchronize_session=False)
    )).all()
    return {row.job_id for row in renewed_rows}


async def extend_agent_leases_batch(
    db: AsyncSession,
    payload: _ExtendBatchIn,
) -> _ExtendBatchOut:
    """Renew every ACTIVE JOB lease this host still owns, in one request.

    Per-item outcome (mirrors the single-job ``extend_lock`` gate,
    ``_get_valid_runtime_lease``):
      - ``job_not_running``: job is missing or ``status != RUNNING`` — the Agent
        should stop renewing it and drive ``/agent/recovery/sync``.
      - ``lease_missing``: no ACTIVE JOB lease, or the lease is expired
        (grace-held) — the reconciler owns expiry, this path never revives it.
      - ``stale_token``: an ACTIVE lease exists but the fencing_token does not
        match — this Agent has been fenced.
      - ``renewed``: TTL extended; ``expires_at`` is the new deadline.
    """
    now = datetime.now(timezone.utc)
    items = payload.leases
    if not items:
        return _ExtendBatchOut(results=[])
    if len(items) > _LEASE_EXTEND_BATCH_MAX:
        raise BatchTooLarge({
            "code": "LEASE_BATCH_TOO_LARGE",
            "max": _LEASE_EXTEND_BATCH_MAX,
            "received": len(items),
        })

    # Preserve request order; duplicate job_ids collapse to ONE result entry and
    # the LAST occurrence's token wins (the later token is the more recent claim
    # a well-behaved Agent knows about). Documented + tested — not an error.
    ordered_job_ids: List[int] = []
    token_by_job: Dict[int, str] = {}
    for it in items:
        if it.job_id not in token_by_job:
            ordered_job_ids.append(it.job_id)
        token_by_job[it.job_id] = it.fencing_token

    job_rows = (await db.execute(
        select(JobInstance.id, JobInstance.status).where(
            JobInstance.id.in_(ordered_job_ids)
        )
    )).all()
    job_status = {row.id: row.status for row in job_rows}

    lease_rows = (await db.execute(
        select(
            DeviceLease.job_id,
            DeviceLease.fencing_token,
            DeviceLease.expires_at,
            DeviceLease.host_id,
            DeviceLease.agent_instance_id,
        )
        .where(
            DeviceLease.job_id.in_(ordered_job_ids),
            DeviceLease.lease_type == LeaseType.JOB.value,
            DeviceLease.status == LeaseStatus.ACTIVE.value,
        )
    )).all()
    lease_by_job = {row.job_id: row for row in lease_rows}

    prelim: Dict[int, str] = {}
    renewable_job_ids: List[int] = []
    for jid in ordered_job_ids:
        if job_status.get(jid) != JobStatus.RUNNING.value:
            prelim[jid] = "job_not_running"
            continue
        lease = lease_by_job.get(jid)
        if lease is None:
            prelim[jid] = "lease_missing"
            continue
        if lease.fencing_token != token_by_job[jid]:
            prelim[jid] = "stale_token"
            continue
        # Ownership binding: the lease must belong to the requesting host (and
        # agent instance when the Agent reports one). A token leaked across
        # hosts/instances must not renew — classified as fenced.
        if lease.host_id != payload.host_id:
            prelim[jid] = "stale_token"
            continue
        if payload.agent_instance_id and lease.agent_instance_id != payload.agent_instance_id:
            prelim[jid] = "stale_token"
            continue
        expires_at = _as_utc(lease.expires_at)
        if expires_at is None or expires_at <= now:
            # Expired ACTIVE (grace-held) lease: the reconciler is the sole owner
            # of expiry; batch renewal must not revive it (parity with the single
            # endpoint's expires_at>now gate).
            prelim[jid] = "lease_missing"
            continue
        prelim[jid] = "renewable"
        renewable_job_ids.append(jid)

    new_expires = now + timedelta(seconds=_DEVICE_LOCK_LEASE_SECONDS)
    renewed_ids: set[int] = set()
    if renewable_job_ids:
        # #992 / R06-F07: 与 complete_job（先 Job FOR UPDATE，再 release_lease）
        # 统一为 Job → Lease。原先 CAS 先碰 DeviceLease、再 UPDATE Job，
        # 与 complete 交错会形成死锁环。
        locked_running = (await db.execute(
            select(JobInstance.id)
            .where(
                JobInstance.id.in_(renewable_job_ids),
                JobInstance.status == JobStatus.RUNNING.value,
            )
            .order_by(JobInstance.id)
            .with_for_update()
        )).scalars().all()
        still_running = set(locked_running)
        for jid in renewable_job_ids:
            if jid not in still_running:
                prelim[jid] = "job_not_running"
        renewable_job_ids = [jid for jid in renewable_job_ids if jid in still_running]

    if renewable_job_ids:
        cas_pairs = [(jid, token_by_job[jid]) for jid in renewable_job_ids]
        renewed_ids = await _cas_renew_leases(
            db,
            pairs=cas_pairs,
            host_id=payload.host_id,
            agent_instance_id=payload.agent_instance_id,
            now=now,
            new_expires=new_expires,
        )
        if renewed_ids:
            # ADR-0026 invariant ③ (three independent signals):
            #   - lease row CAS above = 租约存活
            #   - EXECUTING_STEP / legacy (null state): request arrival proves
            #     executor process alive → last_execution_heartbeat_at
            #   - WAITING_* / PATROL_SLEEP: Coordinator heartbeat owns the
            #     waiting clock — lease renew must NOT refresh those anchors
            #     (otherwise a dead coordinator is masked by LeaseRenewer).
            #   - progress_marker.last_progress_at → last_progress_at (below)
            # updated_at is never bumped here (#288): the recycler judges
            # liveness solely by the execution signals, so renewals must not
            # be able to refresh a fallback clock.
            item_by_job = {it.job_id: it for it in items}
            by_state: Dict[Optional[str], list[int]] = {}
            for jid in renewed_ids:
                reported = getattr(item_by_job.get(jid), "execution_state", None)
                state_val = reported if reported in _VALID_EXECUTION_STATES else None
                by_state.setdefault(state_val, []).append(jid)

            _waiting = {
                "WAITING_EXECUTION_SLOT", "PATROL_SLEEP", "WAITING_BARRIER",
            }
            for state_val, ids in by_state.items():
                # Pin updated_at to itself so Column.onupdate cannot refresh
                # it — it is no liveness signal (#288).
                values: Dict[str, Any] = {"updated_at": JobInstance.updated_at}
                if state_val is not None:
                    values["execution_state"] = state_val
                if state_val in _waiting:
                    # State only — waiting liveness is PlanRunHost.coordinator_*.
                    pass
                else:
                    # EXECUTING_STEP or unknown/absent state: request arrival
                    # proves the executor process is alive (invariant ③).
                    values["last_execution_heartbeat_at"] = now
                await db.execute(
                    update(JobInstance)
                    .where(
                        JobInstance.id.in_(ids),
                        JobInstance.status == JobStatus.RUNNING.value,
                    )
                    .values(**values)
                    .execution_options(synchronize_session=False)
                )

            progress_rows = []
            for jid in renewed_ids:
                marker = getattr(item_by_job.get(jid), "progress_marker", None)
                ts = _parse_progress_ts((marker or {}).get("last_progress_at"))
                if ts is not None:
                    progress_rows.append({"b_id": jid, "b_progress": ts})
            if progress_rows:
                # Core-table executemany (the ORM-entity form would engage
                # "bulk UPDATE by primary key" and reject WHERE bindparams).
                job_t = JobInstance.__table__
                await db.execute(
                    update(job_t)
                    .where(
                        job_t.c.id == bindparam("b_id"),
                        job_t.c.status == JobStatus.RUNNING.value,
                    )
                    .values(last_progress_at=bindparam("b_progress")),
                    progress_rows,
                )
    await db.commit()

    results: List[_ExtendBatchItemOut] = []
    for jid in ordered_job_ids:
        state = prelim[jid]
        if state == "renewable":
            if jid in renewed_ids:
                results.append(_ExtendBatchItemOut(
                    job_id=jid, status="renewed",
                    expires_at=new_expires.isoformat(),
                ))
            else:
                # CAS miss: released/expired/token-rotated/job-terminal between
                # SELECT and UPDATE. Report a lost status (not renewed) so the
                # Agent recovers instead of trusting a phantom TTL.
                results.append(_ExtendBatchItemOut(job_id=jid, status="lease_missing"))
        else:
            results.append(_ExtendBatchItemOut(job_id=jid, status=state))

    outcome_counts: Dict[str, int] = {}
    for item in results:
        outcome_counts[item.status] = outcome_counts.get(item.status, 0) + 1
    record_lease_extend_batch(outcome_counts, len(results))

    return _ExtendBatchOut(results=results)
