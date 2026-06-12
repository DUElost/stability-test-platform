"""Shared legacy AEE script guardrails."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


LEGACY_AEE_SCRIPT_NAMES = frozenset({"scan_aee", "export_mobilelogs"})
LEGACY_AEE_TEMPLATE_NAMES = frozenset(
    {
        "aimonkey",
        "aimonkey_launcher_lifecycle",
        "monkey_aee",
        "monkey_aee_patrol",
        "monkey_aee_init",
        "monkey_aee_lifecycle",
        "monkey_aee_teardown",
    }
)


def hidden_legacy_plan_ids(db: "Session") -> set[int]:
    from backend.models.plan import PlanStep

    rows = (
        db.query(PlanStep.plan_id)
        .filter(PlanStep.script_name.in_(tuple(LEGACY_AEE_SCRIPT_NAMES)))
        .distinct()
        .all()
    )
    return {int(row[0]) for row in rows}
