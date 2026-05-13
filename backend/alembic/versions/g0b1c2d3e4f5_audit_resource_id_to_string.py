"""widen audit_logs.resource_id from INTEGER to VARCHAR(64)

Host.id 是 String(64) (UUID),旧的 INTEGER resource_id 列无法承载 host
audit 记录,PG 严格类型下 INSERT 直接 InvalidTextRepresentation。
把列宽到 String(64) 后,所有资源类型(host=uuid / job=int / device=int)
都能共用同一列。

Revision ID: g0b1c2d3e4f5
Revises: f9a0b1c2d3e4
Create Date: 2026-05-14 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "g0b1c2d3e4f5"
down_revision = "f9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "audit_logs",
        "resource_id",
        existing_type=sa.Integer(),
        type_=sa.String(length=64),
        existing_nullable=True,
        postgresql_using="resource_id::text",
    )


def downgrade() -> None:
    op.alter_column(
        "audit_logs",
        "resource_id",
        existing_type=sa.String(length=64),
        type_=sa.Integer(),
        existing_nullable=True,
        postgresql_using="resource_id::integer",
    )
