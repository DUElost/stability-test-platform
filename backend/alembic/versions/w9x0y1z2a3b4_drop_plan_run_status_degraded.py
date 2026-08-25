"""Remove orphan PlanRunStatus.DEGRADED from plan_run_status enum (#417 / #291).

#291 removed DEGRADED from the Python enum / state machine / frontend. Existing
PostgreSQL databases still carry the orphan enum label created by
w1x2y3z4a5b6 (PLAN_RUN_STATUS_VALUES included DEGRADED). Fresh create_all
already matches the 6-value model; this migration aligns upgraded DBs.

PostgreSQL has no ``ALTER TYPE ... DROP VALUE`` (docs: enum values cannot be
removed short of drop+recreate). Procedure: assert zero DEGRADED rows → create
replacement enum → cast ``plan_run.status`` → drop old → rename.

Revision ID: w9x0y1z2a3b4
Revises: v8w9x0y1z2a3
Create Date: 2026-08-25
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "w9x0y1z2a3b4"
down_revision = "v8w9x0y1z2a3"
branch_labels = None
depends_on = None

# Match backend.models.enums.PlanRunStatus declaration order (create_all parity).
PLAN_RUN_STATUS_VALUES = (
    "RUNNING",
    "SUCCESS",
    "PARTIAL_SUCCESS",
    "FAILED",
    "QUEUED",
    "PRECHECK",
)

# Downgrade restores the historical label (append-only; position not load-bearing).
PLAN_RUN_STATUS_VALUES_WITH_DEGRADED = PLAN_RUN_STATUS_VALUES + ("DEGRADED",)


def _enum_has_label(bind, typname: str, label: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT 1 FROM pg_enum e "
                "JOIN pg_type t ON e.enumtypid = t.oid "
                "WHERE t.typname = :typname AND e.enumlabel = :label"
            ),
            {"typname": typname, "label": label},
        ).scalar()
    )


def _replace_plan_run_status_enum(bind, new_values: tuple[str, ...]) -> None:
    """Recreate plan_run_status with ``new_values`` (only column: plan_run.status)."""
    tmp_name = "plan_run_status_new"
    # Clear a half-finished prior attempt (failed mid-migration).
    op.execute(f"DROP TYPE IF EXISTS {tmp_name}")
    new_enum = postgresql.ENUM(*new_values, name=tmp_name)
    new_enum.create(bind, checkfirst=False)

    # Partial index predicate binds 'QUEUED'::plan_run_status (old oid); drop it
    # before the column cast or ALTER fails with "operator does not exist:
    # plan_run_status_new = plan_run_status".
    op.execute("DROP INDEX IF EXISTS idx_plan_run_admission_queue")

    op.execute("ALTER TABLE plan_run ALTER COLUMN status DROP DEFAULT")
    op.execute(
        f"ALTER TABLE plan_run "
        f"ALTER COLUMN status TYPE {tmp_name} "
        f"USING status::text::{tmp_name}"
    )
    op.execute("ALTER TABLE plan_run ALTER COLUMN status SET DEFAULT 'RUNNING'")

    op.execute("DROP TYPE plan_run_status")
    op.execute(f"ALTER TYPE {tmp_name} RENAME TO plan_run_status")

    # Recreate admission-queue partial index (shape from h3i4j5k6l7m8 retune:
    # priority DESC, enqueued_at; predicate QUEUED).
    op.execute(
        """
        CREATE INDEX idx_plan_run_admission_queue
            ON plan_run (priority DESC, enqueued_at)
         WHERE status = 'QUEUED'
        """
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    if not _enum_has_label(bind, "plan_run_status", "DEGRADED"):
        return  # already aligned (create_all / prior run)

    # Fail loud if any row still uses the label (prod was confirmed 0 at #291).
    count = bind.execute(
        sa.text("SELECT COUNT(*) FROM plan_run WHERE status::text = 'DEGRADED'")
    ).scalar()
    if count:
        raise RuntimeError(
            f"refusing to drop enum label 'DEGRADED': plan_run has {count} row(s) "
            f"with status='DEGRADED'. Remap or delete them first (#417 / #291)."
        )

    _replace_plan_run_status_enum(bind, PLAN_RUN_STATUS_VALUES)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    if _enum_has_label(bind, "plan_run_status", "DEGRADED"):
        return

    _replace_plan_run_status_enum(bind, PLAN_RUN_STATUS_VALUES_WITH_DEGRADED)
