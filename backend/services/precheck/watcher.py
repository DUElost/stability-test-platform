"""Mixed watcher-admin gate for dispatch."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.host import Host
from backend.models.plan_run import PlanRun
from backend.services.plan_dispatcher_core import (
    extract_dispatch_host_watcher_admin_states,
)


def resolve_host_watcher_admin_states_from_db(
    host_ids: list[str], db: Session,
) -> dict[str, bool]:
    rows = db.execute(
        select(Host.id, Host.watcher_admin_active).where(Host.id.in_(host_ids))
    ).all()
    state_map = {
        row.id: True if row.watcher_admin_active is None else bool(row.watcher_admin_active)
        for row in rows
    }
    for host_id in host_ids:
        state_map.setdefault(host_id, True)
    return state_map


def resolve_dispatch_host_watcher_admin_states(
    plan_run: PlanRun, host_ids: list[str], db: Session,
) -> dict[str, bool]:
    snapshot = extract_dispatch_host_watcher_admin_states(
        plan_run.run_context if isinstance(plan_run.run_context, dict) else {}
    )
    if snapshot:
        return {host_id: snapshot.get(host_id, True) for host_id in host_ids}
    return resolve_host_watcher_admin_states_from_db(host_ids, db)


def find_mixed_watcher_inactive_host_ids(
    plan_run: PlanRun, host_ids: list[str], db: Session,
) -> list[str]:
    state_map = resolve_dispatch_host_watcher_admin_states(plan_run, host_ids, db)
    inactive_host_ids = sorted(
        host_id for host_id, is_active in state_map.items() if not is_active
    )
    active_host_ids = [host_id for host_id, is_active in state_map.items() if is_active]
    if inactive_host_ids and active_host_ids:
        return inactive_host_ids
    return []


_find_mixed_watcher_inactive_host_ids = find_mixed_watcher_inactive_host_ids
