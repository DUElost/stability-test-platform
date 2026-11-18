"""B8 G8 — 受控兼容补跑登记（ADR-0062 D15 第 5–7 条）。

新增单数表 ``compat_rerun``：Owner 显式发起的单个 DEDUP_FIRST 分区兼容补跑。
表只记录来源、覆盖、缺失清单与独立产物路径；不修改 delivery_version、
device_log_event 或累计发生事实。纯 additive，downgrade 完整撤销。

Revision ID: b8g8c1d2e3f4
Revises:    b8g6d0e1f234
Create Date: 2026-11-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b8g8c1d2e3f4"
down_revision = "b8g6d0e1f234"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "compat_rerun",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_run_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("request_key", sa.String(length=128), nullable=False),
        sa.Column("requested_by", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "source_delivery_version_id",
            sa.Integer(),
            sa.ForeignKey("delivery_version.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_delivery_version_no", sa.Integer(), nullable=True),
        sa.Column(
            "coverage_scope",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "source_records",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "missing_items",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "is_partial",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("artifact_dir", sa.String(length=1024), nullable=True),
        sa.Column("merge_xls_path", sa.String(length=1024), nullable=True),
        sa.Column("manifest_path", sa.String(length=1024), nullable=True),
        sa.Column("error", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "plan_run_id",
            "platform",
            "request_key",
            name="uq_compat_rerun_run_platform_request",
        ),
    )
    op.create_index(
        "idx_compat_rerun_run_platform",
        "compat_rerun",
        ["plan_run_id", "platform"],
    )


def downgrade() -> None:
    op.drop_index("idx_compat_rerun_run_platform", table_name="compat_rerun")
    op.drop_table("compat_rerun")
