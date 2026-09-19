"""widen job_log_signal.source to VARCHAR(32)

Revision ID: t9u0v1w2x3y4
Revises: f6a7b8c9d0e1
Create Date: 2026-09-19

#2792: the agent-side contract whitelist (backend/agent/watcher/contracts.py,
#806) admits ``reconciler_rollback`` (19 chars) while the column stayed
VARCHAR(16) — every reconciler self-shutdown signal aborts the whole batch
INSERT (`value too long for type character varying(16)`), killing co-batched
signals (the #1048 batch-poisoning shape). Widen to 32 to cover the full
whitelist with headroom. VARCHAR widening is metadata-only in PostgreSQL
(no table rewrite).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "t9u0v1w2x3y4"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "job_log_signal",
        "source",
        existing_type=sa.String(16),
        existing_nullable=False,
        type_=sa.String(32),
    )


def downgrade() -> None:
    # 收缩前必须先清掉超宽行，否则 PG 拒绝缩列（#2792 反向不可达）。
    op.execute("DELETE FROM job_log_signal WHERE length(source) > 16")
    op.alter_column(
        "job_log_signal",
        "source",
        existing_type=sa.String(32),
        existing_nullable=False,
        type_=sa.String(16),
    )
