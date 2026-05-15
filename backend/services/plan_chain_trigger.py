"""Plan chain trigger — ADR-0020.

When a PlanRun reaches a terminal state (SUCCESS, PARTIAL_SUCCESS),
automatically dispatch the next Plan in the chain.

Idempotency: uses SELECT ... FOR UPDATE on the parent PlanRun and checks
``next_plan_triggered`` before dispatching, then writes ``next_plan_triggered=true``
in the same transaction.  The ``uniq_plan_run_chain_child`` partial unique
index provides a second layer of protection.
"""

from __future__ import annotations

import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services.plan_dispatcher import dispatch_plan, PlanDispatchError as AsyncPlanDispatchError
from backend.services.plan_dispatcher_sync import dispatch_plan_sync, PlanDispatchError as SyncPlanDispatchError

logger = logging.getLogger(__name__)

TRIGGERABLE_TERMINAL_STATUSES = {"SUCCESS", "PARTIAL_SUCCESS"}


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

    # Lock the parent PlanRun row to serialize chain dispatch.
    locked = (await db.execute(
        select(PlanRun).where(PlanRun.id == plan_run.id).with_for_update()
    )).scalar()
    if locked is None or locked.next_plan_triggered is True:
        return None  # Already triggered (idempotent)

    # Aggregate device_ids from child JobInstances of the parent PlanRun
    device_rows = (await db.execute(
        select(JobInstance.device_id).where(
            JobInstance.plan_run_id == plan_run.id
        )
    )).all()
    device_ids = list({r.device_id for r in device_rows})
    if not device_ids:
        logger.warning("plan_chain_trigger_no_devices plan_run=%d", plan_run.id)
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
        return None

    # Mark parent as triggered
    await db.execute(
        update(PlanRun)
        .where(PlanRun.id == plan_run.id)
        .values(next_plan_triggered=True)
    )
    await db.commit()

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

    # (1) Atomically mark triggered + commit — prevents concurrent duplicate dispatch.
    #     Uses UPDATE ... RETURNING instead of with_for_update() because
    #     dispatch_plan_sync() commits internally and would release the lock
    #     before next_plan_triggered is persisted.
    result = db.execute(
        update(PlanRun)
        .where(PlanRun.id == plan_run.id)
        .where(PlanRun.next_plan_triggered.is_(False))
        .values(next_plan_triggered=True)
        .returning(PlanRun.id)
    )
    locked_id = result.scalar()
    db.commit()  # 释放锁；next_plan_triggered 已持久化
    if locked_id is None:
        # 另一个并发调用已经标记/触发
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
        return None

    logger.info(
        "plan_chain_triggered_sync parent=%d child=%d chain_index=%d",
        plan_run.id, child.id, chain_index,
    )
    return child
