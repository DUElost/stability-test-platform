"""host_script_presence —— host × 脚本版本的在位矩阵（#2958 第五道闸）

Revision ID: 9f8e7d6c5b4a
Revises: t9u0v1w2x3y4
Create Date: 2026-09-21

常设 sweep 的落地面：一行 = 一台 host × 一个目标版本的当前态
（``present / missing / mismatch / unknown / n_a / maintenance`` 五态+维护态）。

- 唯一键 ``(host_id, name, version)`` 支撑整轮 upsert（每轮刷新 checked_at/sweep_id）；
- ``name``/``version`` **不建 FK**：版本可退役，账本要留痕（与 `job_instance` 的
  script_name/script_version 同款冗余引用）；
- 索引：``state``（指标/告警按态聚合）与 ``checked_at``（新鲜度 = min(checked_at)）；
- 建表为新增对象，不重写存量行；down 仅删本表。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "9f8e7d6c5b4a"
down_revision = "t9u0v1w2x3y4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "host_script_presence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("host_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("detail", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sweep_id", sa.String(length=32), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(
            ["host_id"],
            ["host.id"],
            name="fk_host_script_presence_host_id",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.UniqueConstraint("host_id", "name", "version", name="uq_host_script_presence_key"),
    )
    op.create_index("idx_host_script_presence_state", "host_script_presence", ["state"])
    op.create_index("idx_host_script_presence_checked", "host_script_presence", ["checked_at"])


def downgrade() -> None:
    op.drop_index("idx_host_script_presence_checked", table_name="host_script_presence")
    op.drop_index("idx_host_script_presence_state", table_name="host_script_presence")
    op.drop_table("host_script_presence")
