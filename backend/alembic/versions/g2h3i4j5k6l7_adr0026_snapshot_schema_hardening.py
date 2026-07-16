"""ADR-0026 P1 step 1.1 — snapshot-table schema hardening.

Two integrity gaps that must close before the admission-queue feature flag
ever creates snapshot rows (reviewer-required, see ADR-0026):

1. ON DELETE CASCADE for the pure-snapshot child tables. Retention cleanup
   (and test/maintenance bulk deletes) delete PlanRun rows directly
   (backend/scheduler/cron_scheduler.py run_retention_cleanup); without
   cascade those deletes would start failing the moment snapshot rows exist.

2. Composite consistency FK: (plan_run_host_id, plan_run_id) →
   plan_run_host(id, plan_run_id). With two independent FKs a target-device
   row could legally reference PlanRun A but a host-group row belonging to
   PlanRun B; the composite FK makes that combination impossible.

Both changes are metadata-only at this point — no production path writes
these tables yet (schema landed in e0f1a2b3c4d5, still dormant).

Revision ID: g2h3i4j5k6l7
Revises: e0f1a2b3c4d5
Create Date: 2026-07-16
"""

from alembic import op
import sqlalchemy as sa


revision = "g2h3i4j5k6l7"
down_revision = "e0f1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── plan_run_host.plan_run_id → CASCADE ────────────────────────────────
    op.drop_constraint("plan_run_host_plan_run_id_fkey", "plan_run_host", type_="foreignkey")
    op.create_foreign_key(
        None, "plan_run_host", "plan_run",
        ["plan_run_id"], ["id"], ondelete="CASCADE",
    )
    # Referenced target for the composite FK below: (id, plan_run_id) must be
    # unique (id alone already is — this composite is for FK addressing only).
    op.create_unique_constraint(
        "uq_plan_run_host_id_plan_run", "plan_run_host", ["id", "plan_run_id"],
    )

    # ── plan_run_target_device.plan_run_id → CASCADE ───────────────────────
    op.drop_constraint("plan_run_target_device_plan_run_id_fkey", "plan_run_target_device", type_="foreignkey")
    op.create_foreign_key(
        None, "plan_run_target_device", "plan_run",
        ["plan_run_id"], ["id"], ondelete="CASCADE",
    )

    # ── composite consistency FK replaces the simple host-group FK ─────────
    op.drop_constraint("plan_run_target_device_plan_run_host_id_fkey", "plan_run_target_device", type_="foreignkey")
    op.create_foreign_key(
        None, "plan_run_target_device", "plan_run_host",
        ["plan_run_host_id", "plan_run_id"], ["id", "plan_run_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "plan_run_target_device_plan_run_host_id_plan_run_id_fkey",
        "plan_run_target_device", type_="foreignkey",
    )
    op.create_foreign_key(
        None, "plan_run_target_device", "plan_run_host",
        ["plan_run_host_id"], ["id"],
    )

    op.drop_constraint("plan_run_target_device_plan_run_id_fkey", "plan_run_target_device", type_="foreignkey")
    op.create_foreign_key(
        None, "plan_run_target_device", "plan_run",
        ["plan_run_id"], ["id"],
    )

    op.drop_constraint("uq_plan_run_host_id_plan_run", "plan_run_host", type_="unique")
    op.drop_constraint("plan_run_host_plan_run_id_fkey", "plan_run_host", type_="foreignkey")
    op.create_foreign_key(
        None, "plan_run_host", "plan_run",
        ["plan_run_id"], ["id"],
    )
