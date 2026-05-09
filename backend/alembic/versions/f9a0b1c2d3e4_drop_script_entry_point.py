"""Drop the dead ``script.entry_point`` column.

ADR-0020 follow-up — ``entry_point`` was originally intended to point at
the script's callable inside a Python module ("module:func"), but the
flat-layout convergence (`<name>/v<version>/<entry>.{py,sh,bat}`) made
``nfs_path`` self-sufficient: the runner invokes the entry file directly
via subprocess, without ever resolving an entry symbol.

Audit before drop (2026-05-08):
  * pipeline_engine / ScriptRegistry never read it.
  * scanner wrote empty string; tests wrote empty string.
  * frontend types declared ``entry_point?: string | null`` but no
    component read the value.

This migration drops the column. Downgrade re-adds it nullable.

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-05-08
"""

from alembic import op
import sqlalchemy as sa


revision = "f9a0b1c2d3e4"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None


def _has_column(inspector, table: str, column: str) -> bool:
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "script" not in set(inspector.get_table_names()):
        return

    if _has_column(inspector, "script", "entry_point"):
        op.drop_column("script", "entry_point")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "script" not in set(inspector.get_table_names()):
        return

    if not _has_column(inspector, "script", "entry_point"):
        op.add_column("script", sa.Column("entry_point", sa.String(length=256), nullable=True))
