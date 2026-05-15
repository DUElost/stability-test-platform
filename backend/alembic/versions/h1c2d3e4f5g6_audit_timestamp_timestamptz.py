"""store audit_logs.timestamp as timestamptz

AuditLog 默认写入 `datetime.now(timezone.utc)`，而旧列类型是
`TIMESTAMP WITHOUT TIME ZONE`。在 asyncpg 路径下这会直接触发
offset-aware / offset-naive 类型错误。审计时间统一按 UTC 带时区存储。

Revision ID: h1c2d3e4f5g6
Revises: g0b1c2d3e4f5
Create Date: 2026-05-15 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "h1c2d3e4f5g6"
down_revision = "g0b1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "audit_logs",
        "timestamp",
        existing_type=sa.DateTime(),
        type_=sa.DateTime(timezone=True),
        existing_nullable=False,
        postgresql_using="timestamp AT TIME ZONE 'UTC'",
    )


def downgrade() -> None:
    op.alter_column(
        "audit_logs",
        "timestamp",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.DateTime(),
        existing_nullable=False,
        postgresql_using="timestamp AT TIME ZONE 'UTC'",
    )
