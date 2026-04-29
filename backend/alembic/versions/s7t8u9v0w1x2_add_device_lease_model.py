"""Add device_leases table and capacity columns (ADR-0019 Phase 1).

Revision ID: s7t8u9v0w1x2
Revises:    r6s7t8u9v0w1
Create Date: 2026-04-28
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "s7t8u9v0w1x2"
down_revision = "r6s7t8u9v0w1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. device.lease_generation — fencing token counter (ADR-0019 Phase 1)
    op.add_column(
        "device",
        sa.Column(
            "lease_generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    # 2. host.max_concurrent_jobs — per-host device slot quota
    op.add_column(
        "host",
        sa.Column(
            "max_concurrent_jobs",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("2"),
        ),
    )

    # 3. device_leases table
    op.create_table(
        "device_leases",
        sa.Column("id",                sa.Integer(), primary_key=True),
        sa.Column("device_id",         sa.Integer(), sa.ForeignKey("device.id"), nullable=False),
        sa.Column("job_id",            sa.Integer(), sa.ForeignKey("job_instance.id"), nullable=True),
        sa.Column("host_id",           sa.String(64), sa.ForeignKey("host.id"), nullable=False),
        sa.Column("lease_type",        sa.String(32), nullable=False),
        sa.Column("status",            sa.String(32), nullable=False),
        sa.Column("fencing_token",     sa.String(256), nullable=False),
        sa.Column("lease_generation",  sa.Integer(), nullable=False),
        sa.Column("agent_instance_id", sa.String(64), nullable=False),
        sa.Column("reason",            sa.String(256), nullable=True),
        sa.Column("holder",            sa.String(128), nullable=True),
        sa.Column("acquired_at",       sa.DateTime(timezone=True), nullable=False),
        sa.Column("renewed_at",        sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at",        sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at",       sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_device_leases_host",          "device_leases", ["host_id"])
    op.create_index("idx_device_leases_device_status", "device_leases", ["device_id", "status"])
    op.create_index("idx_device_leases_expires",       "device_leases", ["expires_at"])

    # 4. Partial unique index: at most one ACTIVE lease per device.
    #    SQLAlchemy core does not support WHERE on Index, so we use raw SQL.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_device_leases_active_per_device
            ON device_leases (device_id)
         WHERE status = 'ACTIVE'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_device_leases_active_per_device")
    op.drop_index("idx_device_leases_expires",       table_name="device_leases")
    op.drop_index("idx_device_leases_device_status", table_name="device_leases")
    op.drop_index("idx_device_leases_host",          table_name="device_leases")
    op.drop_table("device_leases")

    op.drop_column("host", "max_concurrent_jobs")
    op.drop_column("device", "lease_generation")
