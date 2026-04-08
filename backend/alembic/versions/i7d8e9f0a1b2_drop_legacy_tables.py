"""Drop legacy tables (Wave 6)

Revision ID: i7d8e9f0a1b2
Revises: h6c7d8e9f0a1
Create Date: 2026-03-26

Prerequisites — verify before running:
  1. All data in legacy tables has been migrated (h6c7d8e9f0a1).
  2. No API traffic is hitting legacy endpoints (/tasks/*, /runs/*/steps).
  3. A database backup exists.

Tables dropped (FK-dependency order):
  workflow_steps, deployments, device_metric_snapshots, workflows,
  run_steps, log_artifacts, task_runs, tasks, task_templates,
  tools, tool_categories

The new canonical tables (host, device, tool, task_template, job_instance,
step_trace, job_artifact, workflow_definition, workflow_run) remain untouched.

Note: hosts/devices legacy int-PK tables are NOT dropped here because
task_schedules.target_device_id still references devices(id) at runtime.
They will be dropped after task_schedules is fully migrated.
"""

from alembic import op
from sqlalchemy import inspect


revision = "i7d8e9f0a1b2"
down_revision = "h6c7d8e9f0a1"
branch_labels = None
depends_on = None


LEGACY_TABLES_ORDERED = [
    # FK-child tables first (reference other legacy tables)
    "workflow_steps",          # FK → workflows, tools, task_runs, devices
    "deployments",             # FK → hosts (legacy int-PK)
    "device_metric_snapshots", # FK → devices (legacy int-PK)
    "workflows",               # referenced by workflow_steps (dropped above)
    # Original Phase 2-5 tables
    "run_steps",               # FK → task_runs
    "log_artifacts",           # FK → task_runs
    "task_runs",               # FK → tasks, hosts, devices
    "tasks",                   # FK → task_templates, tools, devices
    "task_templates",
    "tools",                   # FK → tool_categories
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
