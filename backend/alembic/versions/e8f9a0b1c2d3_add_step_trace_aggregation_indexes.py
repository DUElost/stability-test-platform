"""ADR-0021/ADR-0022 C5a₂: composite indexes on step_trace for PlanRun
aggregation endpoints (timeline / events).

Adds two indexes that are required for the new aggregation endpoints to
serve large PlanRuns (150 devices × 8 days = 1.3M+ trace rows post
ADR-0022) within sub-second latency:

  * ``idx_step_trace_job_stage`` — supports ``GET /plan-runs/{id}/timeline``
    which groups step_trace rows by (job_id, stage) to count
    per-stage success/failed step instances.

  * ``idx_step_trace_job_status_ts`` — supports ``GET /plan-runs/{id}/events``
    which scans failed step traces (status != 'SUCCESS') across all jobs
    of a PlanRun, ordered by ``original_ts``.

Note: ``idx_step_trace_job`` (single-column on job_id) is already present;
these new composite indexes are *additive* and SQL-compatible with
existing queries via leftmost-prefix usage.

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-05-08
"""

from alembic import op
import sqlalchemy as sa


revision = "e8f9a0b1c2d3"
down_revision = "d7e8f9a0b1c2"
branch_labels = None
depends_on = None


_NEW_INDEXES = (
    # name, columns
    ("idx_step_trace_job_stage",     ["job_id", "stage"]),
    ("idx_step_trace_job_status_ts", ["job_id", "status", "original_ts"]),
)


def _indexes(inspector, table):
    return {i["name"] for i in inspector.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "step_trace" not in set(inspector.get_table_names()):
        return

    existing = _indexes(inspector, "step_trace")
    for name, cols in _NEW_INDEXES:
        if name in existing:
            continue
        op.create_index(name, "step_trace", cols)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "step_trace" not in set(inspector.get_table_names()):
        return

    existing = _indexes(inspector, "step_trace")
    for name, _cols in reversed(_NEW_INDEXES):
        if name in existing:
            op.drop_index(name, table_name="step_trace")
