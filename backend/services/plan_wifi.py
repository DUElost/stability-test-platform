"""统一网络供给池与 Plan 的准入校验（ADR-0066 D3 / #4212 B13-U1）。

泛化原 ``plan_wifi.py``：兼容旧 ``wifi_pool_id`` 与新 ``network_pool_id`` 双键，
落实 Phase 1 出口闸门、双向出口匹配、纯存量 Plan 双键限 ``wifi`` 以及新旧混排
交集校验。
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from sqlalchemy.orm import Session

from backend.models.plan import PlanStep
from backend.models.resource_pool import ResourcePool
from backend.services.errors import BadRequest, UnprocessableEntity
from backend.services.plan_dispatcher_core import (
    PlanNetworkRequirements,
    analyze_plan_steps_network_requirements,
)
from backend.services.resource_pool import (
    VALID_EGRESSES,
    VALID_RESOURCE_TYPES,
    pool_egress,
)


def resolve_requested_network_pool_id(
    network_pool_id: Optional[int],
    wifi_pool_id: Optional[int],
) -> tuple[Optional[int], str]:
    """Resolve ``network_pool_id`` + backward-compatible ``wifi_pool_id`` alias.

    Returns ``(resolved_pool_id, label)`` where ``label`` preserves the caller's
    key name for clear error messages. Raises :class:`BadRequest` (400) if both
    are provided and disagree (ADR-0066 D3.1).
    """
    if (
        network_pool_id is not None
        and wifi_pool_id is not None
        and network_pool_id != wifi_pool_id
    ):
        raise BadRequest({
            "code": "CONFLICTING_NETWORK_POOL_IDS",
            "message": (
                f"network_pool_id ({network_pool_id}) conflicts with "
                f"wifi_pool_id ({wifi_pool_id})"
            ),
            "network_pool_id": network_pool_id,
            "wifi_pool_id": wifi_pool_id,
        })
    if network_pool_id is not None:
        return network_pool_id, "network_pool_id"
    if wifi_pool_id is not None:
        return wifi_pool_id, "wifi_pool_id"
    return None, "network_pool_id"


def validate_plan_steps_network_compatibility(
    steps: Iterable[Any],
    *,
    as_unprocessable: bool = True,
) -> PlanNetworkRequirements:
    """Validate that all enabled network steps in a Plan have a non-empty intersection.

    Called both on Plan create/update (422) and PlanRun trigger/preview (400).
    """
    reqs = analyze_plan_steps_network_requirements(steps)
    if reqs.is_contradictory:
        detail = {
            "code": "INVALID_NETWORK_STEP_COMBINATION",
            "message": reqs.conflict_reason or "conflicting network step requirements",
        }
        if as_unprocessable:
            raise UnprocessableEntity(detail)
        raise BadRequest(detail)
    return reqs


def require_active_network_pool(
    db: Session,
    pool_id: int,
    *,
    alias_name: str = "network_pool_id",
) -> ResourcePool:
    """Reject the run up front if the chosen network pool is gone, disabled, or invalid."""
    pool = db.get(ResourcePool, pool_id)
    if (
        pool is None
        or pool.resource_type not in VALID_RESOURCE_TYPES
        or not pool.is_active
    ):
        raise BadRequest(
            f"{alias_name} {pool_id} is not an active network resource pool"
        )
    egress = pool_egress(pool.config)
    if egress not in VALID_EGRESSES:
        raise BadRequest(
            f"{alias_name} {pool_id} has invalid egress {egress!r}"
        )
    if pool.resource_type == "usb_net" and egress == "vpn":
        raise BadRequest({
            "code": "USBNET_VPN_UNSUPPORTED_IN_PHASE1",
            "message": (
                f"{alias_name} {pool_id} uses usb_net + vpn which is disabled in Phase 1"
            ),
        })
    return pool


def require_network_pool_matches_plan(
    db: Session,
    plan_id: int,
    pool_id: Optional[int],
    *,
    alias_name: str = "network_pool_id",
) -> Optional[ResourcePool]:
    """Validate selected pool (or absence of pool) against a Plan's network steps."""
    steps = (
        db.query(PlanStep)
        .filter(PlanStep.plan_id == plan_id)
        .order_by(PlanStep.stage, PlanStep.sort_order)
        .all()
    )
    reqs = validate_plan_steps_network_compatibility(steps, as_unprocessable=False)

    if pool_id is None:
        if reqs.setup_network_required:
            raise BadRequest({
                "code": "NETWORK_POOL_REQUIRED",
                "message": "network_pool_id is required when setup_network.required=true",
            })
        return None

    pool = require_active_network_pool(db, pool_id, alias_name=alias_name)

    if not reqs.has_network_steps:
        raise BadRequest(
            f"{alias_name} requires a plan step that consumes WiFi/network supply "
            "(setup_network, connect_wifi or monkey_setup)"
        )

    if reqs.is_legacy_only and pool.resource_type != "wifi":
        raise BadRequest({
            "code": "LEGACY_PLAN_REQUIRES_WIFI_POOL",
            "message": (
                f"{alias_name} {pool_id} has resource_type={pool.resource_type!r}, "
                "but legacy connect_wifi/monkey_setup plans only accept resource_type='wifi'"
            ),
        })

    if pool.resource_type not in reqs.allowed_types:
        raise BadRequest({
            "code": "NETWORK_POOL_TYPE_MISMATCH",
            "message": (
                f"{alias_name} {pool_id} has resource_type={pool.resource_type!r}, "
                f"which does not satisfy plan required types {sorted(reqs.allowed_types)}"
            ),
        })

    egress = pool_egress(pool.config)
    if egress not in reqs.allowed_egresses:
        raise BadRequest({
            "code": "NETWORK_POOL_EGRESS_MISMATCH",
            "message": (
                f"{alias_name} {pool_id} has egress={egress!r}, "
                f"which does not satisfy plan required egress {sorted(reqs.allowed_egresses)}"
            ),
        })

    return pool


# ── Backward-compatible wrappers ─────────────────────────────────────────────


def require_active_wifi_pool(db: Session, pool_id: int) -> None:
    """Backward-compatible wrapper restricting to active ``wifi`` pools."""
    pool = db.get(ResourcePool, pool_id)
    if pool is None or pool.resource_type != "wifi" or not pool.is_active:
        raise BadRequest(
            f"wifi_pool_id {pool_id} is not an active wifi resource pool"
        )


def require_wifi_pool_matches_plan(db: Session, plan_id: int, pool_id: int) -> None:
    """Backward-compatible wrapper validating a ``wifi_pool_id`` against a Plan."""
    require_network_pool_matches_plan(
        db, plan_id, pool_id, alias_name="wifi_pool_id"
    )
