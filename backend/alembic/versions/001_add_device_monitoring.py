"""Create the legacy baseline and add device monitoring fields.

Revision ID: 001_add_device_monitoring
Revises:
Create Date: 2026-01-24 11:30:00.000000

The first version of this migration assumed that the legacy tables had been
created by ``Base.metadata.create_all`` before Alembic was introduced.  That
made ``alembic upgrade head`` fail on a genuinely empty database.  The
baseline tables below are the small legacy surface required by the following
migrations; later revisions create the current singular-table schema and
retire these tables where appropriate.

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '001_add_device_monitoring'
down_revision = None
branch_labels = None
depends_on = None


_MONITORING_COLUMNS = {
    "adb_state": sa.Column("adb_state", sa.String(length=32), nullable=True),
    "adb_connected": sa.Column("adb_connected", sa.Boolean(), nullable=True),
    "battery_level": sa.Column("battery_level", sa.Integer(), nullable=True),
    "battery_temp": sa.Column("battery_temp", sa.Integer(), nullable=True),
    "temperature": sa.Column("temperature", sa.Integer(), nullable=True),
    "wifi_rssi": sa.Column("wifi_rssi", sa.Integer(), nullable=True),
    "wifi_ssid": sa.Column("wifi_ssid", sa.String(length=128), nullable=True),
    "cpu_usage": sa.Column("cpu_usage", sa.Float(), nullable=True),
    "mem_total": sa.Column("mem_total", sa.BigInteger(), nullable=True),
    "mem_used": sa.Column("mem_used", sa.BigInteger(), nullable=True),
    "disk_total": sa.Column("disk_total", sa.BigInteger(), nullable=True),
    "disk_used": sa.Column("disk_used", sa.BigInteger(), nullable=True),
    "hardware_updated_at": sa.Column("hardware_updated_at", sa.DateTime(), nullable=True),
}


def _table_exists(bind, table_name: str) -> bool:
    return sa.inspect(bind).has_table(table_name)


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in sa.inspect(bind).get_columns(table_name))


def _index_exists(bind, table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in sa.inspect(bind).get_indexes(table_name))


def _create_legacy_baseline(bind) -> None:
    """Create only the tables that predate the first Alembic revision.

    ``b0f805bf6cee`` creates ``task_templates`` and ``log_artifacts`` and
    adds several columns to ``tasks``/``task_runs``.  Keeping those objects
    out of this baseline avoids duplicate-column/table errors while retaining
    the dependency order needed by the migration chain.
    """
    if not _table_exists(bind, "hosts"):
        op.create_table(
            "hosts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("ip", sa.String(64), nullable=False),
            sa.Column("ssh_port", sa.Integer(), server_default="22"),
            sa.Column("ssh_user", sa.String(64), nullable=True),
            sa.Column("ssh_auth_type", sa.String(32), server_default="password"),
            sa.Column("ssh_key_path", sa.String(256), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="OFFLINE"),
            sa.Column("last_heartbeat", sa.DateTime(), nullable=True),
            sa.Column("extra", sa.JSON(), nullable=True),
            sa.Column("mount_status", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("name", name="uq_hosts_name"),
        )
        op.create_index("ix_hosts_ip", "hosts", ["ip"])

    if not _table_exists(bind, "devices"):
        op.create_table(
            "devices",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("serial", sa.String(128), nullable=False),
            sa.Column("host_id", sa.Integer(), sa.ForeignKey("hosts.id"), nullable=True),
            sa.Column("model", sa.String(128), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="OFFLINE"),
            sa.Column("lock_run_id", sa.Integer(), nullable=True),
            sa.Column("lock_expires_at", sa.DateTime(), nullable=True),
            sa.Column("last_seen", sa.DateTime(), nullable=True),
            sa.Column("tags", sa.JSON(), nullable=True),
            sa.Column("extra", sa.JSON(), nullable=True),
            # h6c7d8e9f0a1 copies this timestamp into the singular device table.
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("serial", name="uq_devices_serial"),
        )
        op.create_index("ix_devices_serial", "devices", ["serial"])
        op.create_index("ix_dev_host_status", "devices", ["host_id", "status"])

    if not _table_exists(bind, "tasks"):
        op.create_table(
            "tasks",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("type", sa.String(32), nullable=False),
            # Added by b0f805bf6cee after task_templates is created.
            sa.Column("params", sa.JSON(), nullable=True),
            sa.Column("target_device_id", sa.Integer(), sa.ForeignKey("devices.id"), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
            sa.Column("priority", sa.Integer(), server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )

    if not _table_exists(bind, "task_runs"):
        op.create_table(
            "task_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=False),
            sa.Column("host_id", sa.Integer(), sa.ForeignKey("hosts.id"), nullable=False),
            sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id"), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="QUEUED"),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("exit_code", sa.Integer(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("log_summary", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )

    # A few pre-Alembic installations were created from an older ORM model
    # without this column, but h6c7d8e9f0a1 reads it during legacy backfill.
    if not _column_exists(bind, "devices", "created_at"):
        op.add_column("devices", sa.Column("created_at", sa.DateTime(), nullable=True))


def upgrade() -> None:
    bind = op.get_bind()
    _create_legacy_baseline(bind)

    for name, column in _MONITORING_COLUMNS.items():
        if not _column_exists(bind, "devices", name):
            op.add_column("devices", column)

    if not _index_exists(bind, "devices", "ix_devices_host_last_seen"):
        op.create_index("ix_devices_host_last_seen", "devices", ["host_id", "last_seen"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "devices"):
        return
    if _index_exists(bind, "devices", "ix_devices_host_last_seen"):
        op.drop_index("ix_devices_host_last_seen", table_name="devices")
    for name in reversed(tuple(_MONITORING_COLUMNS)):
        if _column_exists(bind, "devices", name):
            op.drop_column("devices", name)
