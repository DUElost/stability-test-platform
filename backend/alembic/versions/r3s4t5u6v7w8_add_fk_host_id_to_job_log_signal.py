"""add FK host_id to job_log_signal

Revision ID: r3s4t5u6v7w8
Revises: q2r3s4t5u6v7
Create Date: 2026-06-13

B2 fix: JobLogSignal.host_id was declared as a plain String column with no
FK reference, meaning host deletion would leave orphan rows.  Add the
foreign-key constraint so CASCADE delete propagates correctly.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "r3s4t5u6v7w8"
down_revision = "q2r3s4t5u6v7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add foreign key constraint on job_log_signal.host_id → host.id
    op.create_foreign_key(
        "fk_job_log_signal_host_id",
        source_table="job_log_signal",
        referent_table="host",
        local_cols=["host_id"],
        remote_cols=["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_job_log_signal_host_id", "job_log_signal", type_="foreignkey")
