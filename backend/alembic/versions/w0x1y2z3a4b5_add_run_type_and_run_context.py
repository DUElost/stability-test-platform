"""Add run_type and run_context columns to workflow_run.

Split result_summary dual-semantics:
  - run_type    → "script_execution" | "workflow" (extensible)
  - run_context → creation metadata (items, sequence_id, on_failure)
  - result_summary → aggregation result only (total, completed, failed, pass_rate)

Backfill: runs with result_summary.mode=="script_execution" or triggered_by=="script_execution"
get run_type="script_execution"; all others get "workflow".
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "w0x1y2z3a4b5"
down_revision = "5790a8de0a87"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "workflow_run",
        sa.Column("run_type", sa.String(64), nullable=True),
    )
    op.add_column(
        "workflow_run",
        sa.Column("run_context", postgresql.JSONB(), nullable=True),
    )

    op.execute(
        sa.text(
            """UPDATE workflow_run
               SET run_type = CASE
                   WHEN result_summary->>'mode' = 'script_execution'
                     OR triggered_by = 'script_execution'
                   THEN 'script_execution'
                   ELSE 'workflow'
               END"""
        )
    )

    op.execute(
        sa.text(
            """UPDATE workflow_run
               SET run_context = result_summary
               WHERE run_type = 'script_execution'"""
        )
    )


def downgrade():
    op.drop_column("workflow_run", "run_context")
    op.drop_column("workflow_run", "run_type")
