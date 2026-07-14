"""Plan chain trigger — ADR-0020.

When a PlanRun reaches a terminal state (SUCCESS, PARTIAL_SUCCESS),
automatically dispatch the next Plan in the chain.

Idempotency: atomically persists ``next_plan_triggered=true`` before dispatch.
The ``uniq_plan_run_chain_child`` partial unique index provides a second
layer of protection.

On dispatch failure the trigger flag is rolled back so a later aggregator
pass can retry, and ``result_summary.chain_dispatch_failed`` records the error.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.tasks.saq_worker import enqueue_sync
from backend.services.plan_dispatcher_sync import (
    initial_dispatch_state,
    prepare_plan_run,
)

logger = logging.getLogger(__name__)

TRIGGERABLE_TERMINAL_STATUSES = {"SUCCESS", "PARTIAL_SUCCESS"}


def _next_plan_id_from_snapshot(plan_run: PlanRun) -> int | None | object:
    """Return next_plan_id from snapshot, or _SNAPSHOT_MISS if absent."""
    snapshot = plan_run.plan_snapshot or {}
    if isinstance(snapshot, dict):
        plan_data = snapshot.get("plan") or {}
        if isinstance(plan_data, dict) and "next_plan_id" in plan_data:
            return plan_data.get("next_plan_id")
    return _SNAPSHOT_MISS


_SNAPSHOT_MISS = object()


def _resolve_next_plan_id_sync(plan_run: PlanRun, db: Session) -> int | None:
    """Read next_plan_id from snapshot; fall back to live Plan for legacy runs."""
    from_snapshot = _next_plan_id_from_snapshot(plan_run)
    if from_snapshot is not _SNAPSHOT_MISS:
        return from_snapshot  # type: ignore[return-value]
    if plan_run.plan_id is not None:
        plan = db.get(Plan, plan_run.plan_id)
        if plan is not None:
            return plan.next_plan_id
    return None


async def _resolve_next_plan_id_async(
    plan_run: PlanRun,
    db: AsyncSession,
) -> int | None:
    from_snapshot = _next_plan_id_from_snapshot(plan_run)
    if from_snapshot is not _SNAPSHOT_MISS:
        return from_snapshot  # type: ignore[return-value]
    if plan_run.plan_id is not None:
        plan = await db.get(Plan, plan_run.plan_id)
        if plan is not None:
            return plan.next_plan_id
    return None


def _record_chain_dispatch_failure(
    plan_run: PlanRun,
    error: Exception | str,
    *,
    child_already_created: bool,
) -> None:
    """Persist failure metadata; only reset flag when child PlanRun wasn't created.

    Why: ADR-0021 dispatch gate ``prepare_plan_run`` writes the child PlanRun
    row BEFORE gate verification. If the gate then fails, the child row stays
    (as FAILED, for audit). Resetting ``next_plan_triggered=False`` would let
    the aggregator retry, but the retry's INSERT would collide with
    ``uniq_plan_run_chain_child`` and loop forever. So when a child row exists
    we only write the audit summary — the chain has, in fact, been triggered.
    """
    summary: dict[str, Any] = dict(plan_run.result_summary or {})
    summary["chain_dispatch_failed"] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "error": str(error)[:500],
        "child_already_created": child_already_created,
    }
    plan_run.result_summary = summary
    flag_modified(plan_run, "result_summary")
    if not child_already_created:
        plan_run.next_plan_triggered = False


async def _rollback_chain_trigger_async(
    db: AsyncSession,
    plan_run_id: int,
    error: Exception | str,
) -> None:
    """#4: rollback 自身 try/except — dispatch_plan 内部 commit 抛错时
    session 可能处于残废态,二次抛会让上层 aggregator 跟着挂。
    """
    try:
        pr = await db.get(PlanRun, plan_run_id)
        if pr is None:
            return
        next_plan_id = await _resolve_next_plan_id_async(pr, db)
        child_exists = False
        if next_plan_id is not None:
            existing = (await db.execute(
                select(PlanRun.id)
                .where(PlanRun.parent_plan_run_id == plan_run_id)
                .where(PlanRun.plan_id == next_plan_id)
                .limit(1)
            )).first()
            child_exists = existing is not None
        _record_chain_dispatch_failure(pr, error, child_already_created=child_exists)
        await db.commit()
        logger.warning(
            "plan_chain_trigger_rolled_back plan_run=%d child_exists=%s error=%s",
            plan_run_id, child_exists, str(error)[:200],
        )
    except Exception:
        logger.exception(
            "plan_chain_trigger_rollback_failed plan_run=%d", plan_run_id,
        )


def _rollback_chain_trigger_sync(
    db: Session,
    plan_run_id: int,
    error: Exception | str,
) -> None:
    """#4: sync 同样防御 — 见 _rollback_chain_trigger_async 注释。"""
    try:
        pr = db.get(PlanRun, plan_run_id)
        if pr is None:
            return
        next_plan_id = _resolve_next_plan_id_sync(pr, db)
        child_exists = False
        if next_plan_id is not None:
            existing = db.execute(
                select(PlanRun.id)
                .where(PlanRun.parent_plan_run_id == plan_run_id)
                .where(PlanRun.plan_id == next_plan_id)
                .limit(1)
            ).first()
            child_exists = existing is not None
        _record_chain_dispatch_failure(pr, error, child_already_created=child_exists)
        db.commit()
        logger.warning(
            "plan_chain_trigger_sync_rolled_back plan_run=%d child_exists=%s error=%s",
            plan_run_id, child_exists, str(error)[:200],
        )
    except Exception:
        logger.exception(
            "plan_chain_trigger_sync_rollback_failed plan_run=%d", plan_run_id,
        )


async def trigger_next_plan(
    plan_run: PlanRun,
    db: AsyncSession,
) -> PlanRun | None:
    """Create child Run + parent flag atomically; enqueue its gate post-commit."""
    parent = (await db.execute(
        select(PlanRun)
        .where(PlanRun.id == plan_run.id)
        .with_for_update(key_share=True)
    )).scalar_one_or_none()
    if parent is None or parent.status not in TRIGGERABLE_TERMINAL_STATUSES:
        return None

    next_plan_id = await _resolve_next_plan_id_async(parent, db)
    if next_plan_id is None:
        return None
    existing = (await db.execute(
        select(PlanRun)
        .where(
            PlanRun.parent_plan_run_id == parent.id,
            PlanRun.plan_id == next_plan_id,
        )
        .limit(1)
    )).scalar_one_or_none()
    if existing is not None:
        if not parent.next_plan_triggered:
            parent.next_plan_triggered = True
            await db.commit()
        return existing
    if parent.next_plan_triggered:
        return None

    device_ids = list((await db.execute(
        select(JobInstance.device_id).where(
            JobInstance.plan_run_id == parent.id
        )
    )).scalars().unique())
    if not device_ids:
        logger.warning("plan_chain_trigger_no_devices plan_run=%d", parent.id)
        return None

    chain_index = (parent.chain_index or 0) + 1
    try:
        child = await db.run_sync(
            lambda sync_db: prepare_plan_run(
                plan_id=next_plan_id,
                device_ids=device_ids,
                triggered_by=parent.triggered_by or "chain",
                db=sync_db,
                run_type="CHAIN",
                run_context={
                    "triggered_from_plan_run_id": parent.id,
                    "dispatch_state": initial_dispatch_state(),
                },
                parent_plan_run_id=parent.id,
                root_plan_run_id=parent.root_plan_run_id or parent.id,
                chain_index=chain_index,
                commit=False,
            )
        )
        child_id = child.id
        parent.next_plan_triggered = True
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.exception("plan_chain_child_create_failed parent=%d", parent.id)
        await _rollback_chain_trigger_async(db, parent.id, exc)
        return None

    try:
        await asyncio.to_thread(
            enqueue_sync,
            "precheck_and_dispatch_task",
            key=f"precheck:{child_id}",
            timeout=600,
            retries=1,
            required=True,
            plan_run_id=child_id,
        )
    except Exception as exc:
        logger.exception(
            "plan_chain_gate_enqueue_failed parent=%d child=%d",
            parent.id,
            child_id,
        )
        await _rollback_chain_trigger_async(db, parent.id, exc)

    logger.info(
        "plan_chain_triggered parent=%d child=%d chain_index=%d",
        parent.id, child_id, chain_index,
    )
    return await db.get(PlanRun, child_id)


def trigger_next_plan_sync(
    plan_run: PlanRun,
    db: Session,
) -> PlanRun | None:
    """Synchronous atomic child creation + post-commit gate enqueue."""
    parent = db.execute(
        select(PlanRun)
        .where(PlanRun.id == plan_run.id)
        .with_for_update(key_share=True)
    ).scalar_one_or_none()
    if parent is None or parent.status not in TRIGGERABLE_TERMINAL_STATUSES:
        return None

    next_plan_id = _resolve_next_plan_id_sync(parent, db)
    if next_plan_id is None:
        return None
    existing = db.execute(
        select(PlanRun)
        .where(
            PlanRun.parent_plan_run_id == parent.id,
            PlanRun.plan_id == next_plan_id,
        )
        .limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        if not parent.next_plan_triggered:
            parent.next_plan_triggered = True
            db.commit()
        return existing
    if parent.next_plan_triggered:
        return None

    device_ids = list(db.execute(
        select(JobInstance.device_id).where(
            JobInstance.plan_run_id == parent.id
        )
    ).scalars().unique())
    if not device_ids:
        logger.warning(
            "plan_chain_trigger_sync_no_devices plan_run=%d", parent.id,
        )
        return None

    chain_index = (parent.chain_index or 0) + 1
    try:
        child = prepare_plan_run(
            plan_id=next_plan_id,
            device_ids=device_ids,
            triggered_by=parent.triggered_by or "chain",
            db=db,
            run_type="CHAIN",
            run_context={
                "triggered_from_plan_run_id": parent.id,
                "dispatch_state": initial_dispatch_state(),
            },
            parent_plan_run_id=parent.id,
            root_plan_run_id=parent.root_plan_run_id or parent.id,
            chain_index=chain_index,
            commit=False,
        )
        child_id = child.id
        parent.next_plan_triggered = True
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception(
            "plan_chain_child_create_sync_failed parent=%d", parent.id,
        )
        _rollback_chain_trigger_sync(db, parent.id, exc)
        return None

    try:
        enqueue_sync(
            "precheck_and_dispatch_task",
            key=f"precheck:{child_id}",
            timeout=600,
            retries=1,
            required=True,
            plan_run_id=child_id,
        )
    except Exception as exc:
        logger.exception(
            "plan_chain_gate_enqueue_sync_failed parent=%d child=%d",
            parent.id,
            child_id,
        )
        _rollback_chain_trigger_sync(db, parent.id, exc)

    logger.info(
        "plan_chain_triggered_sync parent=%d child=%d chain_index=%d",
        parent.id, child_id, chain_index,
    )
    return db.get(PlanRun, child_id)


def reconcile_chain_trigger_sync(
    plan_run_id: int,
    db: Session,
) -> PlanRun | None:
    """Repair interrupted chain triggers from the durable parent/child rows.

    A crash after the CAS commit but before child creation leaves
    ``next_plan_triggered=true`` with no child.  Post-completion and recycler
    retries call this helper to reset that orphaned flag and dispatch again.
    """
    parent = db.execute(
        select(PlanRun)
        .where(PlanRun.id == plan_run_id)
        .with_for_update(key_share=True)
    ).scalar_one_or_none()
    if parent is None or parent.status not in TRIGGERABLE_TERMINAL_STATUSES:
        return None

    child = db.execute(
        select(PlanRun)
        .where(PlanRun.parent_plan_run_id == parent.id)
        .order_by(PlanRun.id)
        .limit(1)
    ).scalar_one_or_none()
    if child is not None:
        if not parent.next_plan_triggered:
            parent.next_plan_triggered = True
            db.commit()
        return child

    if parent.next_plan_triggered:
        parent.next_plan_triggered = False
        db.commit()
        db.refresh(parent)

    return trigger_next_plan_sync(parent, db)
