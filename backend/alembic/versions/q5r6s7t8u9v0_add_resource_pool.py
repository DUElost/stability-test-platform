"""Add resource_pool and resource_allocation tables.

Revision ID: q5r6s7t8u9v0
Revises:    p4q5r6s7t8u9
Create Date: 2026-04-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "q5r6s7t8u9v0"
down_revision = "p4q5r6s7t8u9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "resource_pool",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("resource_type", sa.String(32), nullable=False, server_default="wifi"),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("max_concurrent_devices", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("host_group", sa.String(128), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "resource_allocation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_instance_id", sa.Integer(), sa.ForeignKey("job_instance.id"), nullable=False),
        sa.Column("resource_pool_id", sa.Integer(), sa.ForeignKey("resource_pool.id"), nullable=False),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("device.id"), nullable=False),
        sa.Column("allocated_params", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_index("ix_resource_allocation_job", "resource_allocation", ["job_instance_id"])
    op.create_index("ix_resource_allocation_pool", "resource_allocation", ["resource_pool_id"])


def downgrade():
    op.drop_index("ix_resource_allocation_pool", table_name="resource_allocation")
    op.drop_index("ix_resource_allocation_job", table_name="resource_allocation")
    op.drop_table("resource_allocation")
    op.drop_table("resource_pool")
