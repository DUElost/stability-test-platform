"""Drop legacy tables (Wave 6)

Revision ID: i7d8e9f0a1b2
Revises: h6c7d8e9f0a1
Create Date: 2026-03-26

Prerequisites — verify before running:
  1. All data in legacy tables has been migrated (h6c7d8e9f0a1).
  2. No API traffic is hitting legacy endpoints (/tasks/*, /runs/*/steps).
  3. A database backup exists.

Tables dropped:
  run_steps, log_artifacts, task_runs, tasks, task_templates,
  tools, tool_categories, hosts (int-PK), devices (int-PK legacy)

The new canonical tables (host, device, tool, task_template, job_instance,
step_trace, job_artifact, workflow_definition, workflow_run) remain untouched.
"""

from alembic import op
from sqlalchemy import inspect


revision = "i7d8e9f0a1b2"
down_revision = "h6c7d8e9f0a1"
branch_labels = None
depends_on = None


LEGACY_TABLES_ORDERED = [
    "run_steps",
    "log_artifacts",
    "task_runs",
    "tasks",
    "task_templates",
    "tools",
    "tool_categories",
]


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    insp = inspect(bind)
    return name in insp.get_table_names()


def upgrade() -> None:
    for table in LEGACY_TABLES_ORDERED:
        if _table_exists(table):
            op.drop_table(table)


def downgrade() -> None:
    raise RuntimeError(
        "Wave 6 DROP migration is irreversible. "
        "Restore from backup to recover legacy tables."
    )
