"""WiFi 池与 Plan 的准入校验（#1519 从 api.routes 下沉，sync 域）。"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.models.plan import PlanStep
from backend.models.resource_pool import ResourcePool
from backend.services.plan_dispatcher_core import plan_steps_consumes_wifi


def require_active_wifi_pool(db: Session, pool_id: int) -> None:
    """Reject the run up front if the chosen WiFi pool is gone or disabled.

    Without this the mistake would only surface inside the admission pump as an
    ``AllocationError``, i.e. after the PlanRun is already QUEUED.
    """
    pool = db.get(ResourcePool, pool_id)
    if pool is None or pool.resource_type != "wifi" or not pool.is_active:
        raise HTTPException(
            status_code=400,
            detail=f"wifi_pool_id {pool_id} is not an active wifi resource pool",
        )


def require_wifi_pool_matches_plan(db: Session, plan_id: int, pool_id: int) -> None:
    """Reject wifi_pool_id when the Plan has no step that can consume WiFi."""
    steps = (
        db.query(PlanStep)
        .filter(PlanStep.plan_id == plan_id)
        .order_by(PlanStep.stage, PlanStep.sort_order)
        .all()
    )
    if not plan_steps_consumes_wifi(steps):
        raise HTTPException(
            status_code=400,
            detail=(
                "wifi_pool_id requires a plan step that consumes WiFi "
                "(connect_wifi or monkey_setup)"
            ),
        )
