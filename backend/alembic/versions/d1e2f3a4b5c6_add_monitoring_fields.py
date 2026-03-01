"""add monitoring fields to host and device tables

Revision ID: d1e2f3a4b5c6
Revises: a1b2c3d4e5f6
Create Date: 2026-02-28

Phase B: Add monitoring/SSH fields to new STP host/device tables (all nullable).
"""

from alembic import op
import sqlalchemy as sa

revision = "d1e2f3a4b5c6"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # host table: SSH + display fields
    op.add_column("host", sa.Column("name", sa.String(128), nullable=True))
    op.add_column("host", sa.Column("ip", sa.String(64), nullable=True))
    op.add_column("host", sa.Column("ssh_port", sa.Integer(), nullable=True, server_default="22"))
    op.add_column("host", sa.Column("ssh_user", sa.String(64), nullable=True))
    op.add_column("host", sa.Column("ssh_auth_type", sa.String(32), nullable=True, server_default="password"))
    op.add_column("host", sa.Column("ssh_key_path", sa.String(256), nullable=True))
    op.add_column("host", sa.Column("extra", sa.JSON(), nullable=True))
    op.add_column("host", sa.Column("mount_status", sa.JSON(), nullable=True))
    op.add_column("host", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))

    # device table: lock + ADB + monitoring fields
    op.add_column("device", sa.Column("lock_run_id", sa.Integer(), nullable=True))
    op.add_column("device", sa.Column("lock_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("device", sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True))
    op.add_column("device", sa.Column("adb_state", sa.String(32), nullable=True))
    op.add_column("device", sa.Column("adb_connected", sa.Boolean(), nullable=True))
    op.add_column("device", sa.Column("battery_level", sa.Integer(), nullable=True))
    op.add_column("device", sa.Column("battery_temp", sa.Integer(), nullable=True))
    op.add_column("device", sa.Column("temperature", sa.Integer(), nullable=True))
    op.add_column("device", sa.Column("wifi_rssi", sa.Integer(), nullable=True))
    op.add_column("device", sa.Column("wifi_ssid", sa.String(128), nullable=True))
    op.add_column("device", sa.Column("network_latency", sa.Float(), nullable=True))
    op.add_column("device", sa.Column("cpu_usage", sa.Float(), nullable=True))
    op.add_column("device", sa.Column("mem_total", sa.BigInteger(), nullable=True))
    op.add_column("device", sa.Column("mem_used", sa.BigInteger(), nullable=True))
    op.add_column("device", sa.Column("disk_total", sa.BigInteger(), nullable=True))
    op.add_column("device", sa.Column("disk_used", sa.BigInteger(), nullable=True))
    op.add_column("device", sa.Column("hardware_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("device", sa.Column("extra", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("device", "extra")
    op.drop_column("device", "hardware_updated_at")
    op.drop_column("device", "disk_used")
    op.drop_column("device", "disk_total")
    op.drop_column("device", "mem_used")
    op.drop_column("device", "mem_total")
    op.drop_column("device", "cpu_usage")
    op.drop_column("device", "network_latency")
    op.drop_column("device", "wifi_ssid")
    op.drop_column("device", "wifi_rssi")
    op.drop_column("device", "temperature")
    op.drop_column("device", "battery_temp")
    op.drop_column("device", "battery_level")
    op.drop_column("device", "adb_connected")
    op.drop_column("device", "adb_state")
    op.drop_column("device", "last_seen")
    op.drop_column("device", "lock_expires_at")
    op.drop_column("device", "lock_run_id")

    op.drop_column("host", "updated_at")
    op.drop_column("host", "mount_status")
    op.drop_column("host", "extra")
    op.drop_column("host", "ssh_key_path")
    op.drop_column("host", "ssh_auth_type")
    op.drop_column("host", "ssh_user")
    op.drop_column("host", "ssh_port")
    op.drop_column("host", "ip")
    op.drop_column("host", "name")
