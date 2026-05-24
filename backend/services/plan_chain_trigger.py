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

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services.plan_dispatcher import dispatch_plan, PlanDispatchError as AsyncPlanDispatchError
from backend.services.plan_dispatcher_sync import dispatch_plan_sync, PlanDispatchError as SyncPlanDispatchError

logger = logging.getLogger(__name__)

TRIGGERABLE_TERMINAL_STATUSES = {"SUCCESS", "PARTIAL_SUCCESS"}


def _record_chain_dispatch_failure(
    plan_run: PlanRun,
    error: Exception | str,
) -> None:
    """Rollback ``next_plan_triggered`` and persist failure metadata."""
    plan_run.next_plan_triggered = False
    summary: dict[str, Any] = dict(plan_run.result_summary or {})
    summary["chain_dispatch_failed"] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "error": str(error)[:500],
    }
    plan_run.result_summary = summary
    flag_modified(plan_run, "result_summary")


async def _rollback_chain_trigger_async(
    db: AsyncSession,
    plan_run_id: int,
    error: Exception | str,
) -> None:
    pr = await db.get(PlanRun, plan_run_id)
    if pr is None:
        return
    _record_chain_dispatch_failure(pr, error)
    await db.commit()
    logger.warning(
        "plan_chain_trigger_rolled_back plan_run=%d error=%s",
        plan_run_id, str(error)[:200],
    )


def _rollback_chain_trigger_sync(
    db: Session,
    plan_run_id: int,
    error: Exception | str,
) -> None:
    pr = db.get(PlanRun, plan_run_id)
    if pr is None:
        return
    _record_chain_dispatch_failure(pr, error)
    db.commit()
    logger.warning(
        "plan_chain_trigger_sync_rolled_back plan_run=%d error=%s",
        plan_run_id, str(error)[:200],
    )


async def trigger_next_plan(
    plan_run: PlanRun,
    db: AsyncSession,
) -> PlanRun | None:
    """If *plan_run* is in a triggerable terminal status and has a next Plan,
    atomically check-and-dispatch the child PlanRun.

    Returns the child PlanRun if triggered, or None.
    """
    if plan_run.status not in TRIGGERABLE_TERMINAL_STATUSES:
        return None

    plan = await db.get(Plan, plan_run.plan_id)
    if plan is None or plan.next_plan_id is None:
        return None

    # Aggregate device_ids from child JobInstances of the parent PlanRun.
    device_rows = (await db.execute(
        select(JobInstance.device_id).where(
            JobInstance.plan_run_id == plan_run.id
        )
    )).all()
    device_ids = list({r.device_id for r in device_rows})
    if not device_ids:
        logger.warning("plan_chain_trigger_no_devices plan_run=%d", plan_run.id)
        return None

    # Atomically mark triggered before dispatch. dispatch_plan() commits
    # internally, so row locks would not survive long enough to protect
    # the subsequent next_plan_triggered write.
    result = await db.execute(
        update(PlanRun)
        .where(PlanRun.id == plan_run.id)
        .where(PlanRun.next_plan_triggered.is_(False))
        .values(next_plan_triggered=True)
        .returning(PlanRun.id)
    )
    locked_id = result.scalar()
    await db.commit()
    if locked_id is None:
        return None

    chain_index = (plan_run.chain_index or 0) + 1
    try:
        child = await dispatch_plan(
            plan_id=plan.next_plan_id,
            device_ids=device_ids,
            triggered_by=plan_run.triggered_by or "chain",
            db=db,
            run_type="CHAIN",
            run_context={"triggered_from_plan_run_id": plan_run.id},
            parent_plan_run_id=plan_run.id,
            root_plan_run_id=plan_run.root_plan_run_id or plan_run.id,
            chain_index=chain_index,
        )
    except AsyncPlanDispatchError as exc:
        logger.error("plan_chain_dispatch_failed parent=%d err=%s", plan_run.id, exc)
        await _rollback_chain_trigger_async(db, plan_run.id, exc)
        return None

    logger.info(
        "plan_chain_triggered parent=%d child=%d chain_index=%d",
        plan_run.id, child.id, chain_index,
    )
    return child


def trigger_next_plan_sync(
    plan_run: PlanRun,
    db: Session,
) -> PlanRun | None:
    """Synchronous version of trigger_next_plan for the sync aggregator path."""
    if plan_run.status not in TRIGGERABLE_TERMINAL_STATUSES:
        return None

    plan = db.get(Plan, plan_run.plan_id)
    if plan is None or plan.next_plan_id is None:
        return None

    device_rows = db.execute(
        select(JobInstance.device_id).where(
            JobInstance.plan_run_id == plan_run.id
        )
    ).all()
    device_ids = list({r.device_id for r in device_rows})
    if not device_ids:
        logger.warning("plan_chain_trigger_sync_no_devices plan_run=%d", plan_run.id)
        return None

    # (1) Atomically mark triggered + commit — prevents concurrent duplicate dispatch.
    result = db.execute(
        update(PlanRun)
        .where(PlanRun.id == plan_run.id)
        .where(PlanRun.next_plan_triggered.is_(False))
        .values(next_plan_triggered=True)
        .returning(PlanRun.id)
    )
    locked_id = result.scalar()
    db.commit()
    if locked_id is None:
        return None

    chain_index = (plan_run.chain_index or 0) + 1
    try:
        child = dispatch_plan_sync(
            plan_id=plan.next_plan_id,
            device_ids=device_ids,
            triggered_by=plan_run.triggered_by or "chain",
            db=db,
            run_type="CHAIN",
            run_context={"triggered_from_plan_run_id": plan_run.id},
            parent_plan_run_id=plan_run.id,
            root_plan_run_id=plan_run.root_plan_run_id or plan_run.id,
            chain_index=chain_index,
        )
    except SyncPlanDispatchError as exc:
        logger.error("plan_chain_dispatch_sync_failed parent=%d err=%s", plan_run.id, exc)
        _rollback_chain_trigger_sync(db, plan_run.id, exc)
        return None

    logger.info(
        "plan_chain_triggered_sync parent=%d child=%d chain_index=%d",
        plan_run.id, child.id, chain_index,
    )
    return child
