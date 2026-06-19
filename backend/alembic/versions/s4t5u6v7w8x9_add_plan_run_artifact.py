"""add plan_run_artifact table (ADR-0025 Sprint 4)

Revision ID: s4t5u6v7w8x9
Revises: r3s4t5u6v7w8
Create Date: 2026-06-18

PlanRun 维度产物表：scan/merge 产物（Result_*.xls）与 JobArtifact（Job 维度）
解耦。scan/merge 产物不属于某个 Job，而是 PlanRun 维度的去重/合并结果。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "s4t5u6v7w8x9"
down_revision = "r3s4t5u6v7w8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plan_run_artifact",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_run_id", sa.Integer(), sa.ForeignKey("plan_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("host_id", sa.String(128), nullable=True),
        sa.Column("storage_uri", sa.String(512), nullable=False),
        sa.Column("artifact_type", sa.String(64), nullable=False, server_default="scan_result_xls"),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("plan_run_id", "storage_uri", name="uq_plan_run_artifact_run_storage"),
    )
    op.create_index("idx_plan_run_artifact_run", "plan_run_artifact", ["plan_run_id"])


def downgrade() -> None:
    op.drop_index("idx_plan_run_artifact_run", table_name="plan_run_artifact")
    op.drop_table("plan_run_artifact")
