"""align host.id with ip after 2026-08-15 subnet migration

Revision ID: k8l9m0n1o2p3
Revises: h2i3j4k5l6m7
Create Date: 2026-08-28

Data migration: 20 hosts retain their legacy 8.x/9.x subnet identity in
``host.id`` (e.g. ``172-21-8-192``) while their IP already moved to
172.21.15.x (e.g. 172.21.15.80). This makes the DB host identifier diverge
from the current address the platform uses to reach it, and from the 13
hosts already named ``172-21-15-{last}``.

This migration rewrites ``host.id`` so it matches the current ``host.ip``
(last octet), following the existing ``172-21-15-{last}`` convention, and
rewrites every referencing column in the same transaction.

Why ON UPDATE CASCADE + parent-first: the FK constraints on host.id are
immediate (NO ACTION), so Postgres checks each child UPDATE against the
parent *per statement* — pointing a child at a parent id that doesn't exist
yet fails immediately. The correct single-transaction technique is to make
the FK constraints ``ON UPDATE CASCADE`` and then only UPDATE ``host.id``;
Postgres then rewrites all 6 FK child columns in the same statement, so no
transient referential violation is observed. The two denormalized snapshot
columns (no FK, but a hidden join contract to plan_run_host.host_id /
historical reporting) are updated explicitly afterward.

Affected FK tables (true foreign keys to host.id), constraint renamed but
ON DELETE preserved:
    device, device_leases, job_instance, device_log_event, job_log_signal,
    plan_run_host   -> ON UPDATE CASCADE added

Affected denormalized snapshot columns (updated explicitly):
    plan_run_target_device.host_id_snapshot,
    plan_run_artifact.host_id

The mapping old->new is derived from the DB itself (host.ip last octet),
never hardcoded, so it cannot drift from reality. The whole migration runs
inside the single alembic transaction (transactional DDL), so a failure
rolls everything back.

Verified prerequisites (checked before writing this migration):
  - 20 legacy ids map one-to-one onto distinct 172.21.15.67-.86 IPs, no
    crossing/duplication, all ONLINE.
  - Each host reports a unique /etc/machine-id and a single 172.21.15.x
    address (no VM clones / IP collisions).

Downgrade is a no-op: reversing a historical data normalization would
break the running control-plane + agent room keys; the change is meant to
be permanent. The ALTERed FK constraints are left ON UPDATE CASCADE (an
unambiguous improvement; read-the-current-constraint semantics unchanged).
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "k8l9m0n1o2p3"
down_revision = "h2i3j4k5l6m7"
branch_labels = None
depends_on = None

# (table, canonical_constraint_name, on_delete) — rebuild each FK on host.id
# with ON UPDATE CASCADE, preserving the existing ON DELETE rule.
#
# #510：约束的**现存名**在两条路径上不一致——存量库是 PG 默认名
# （{table}_host_id_fkey），空库链路上 job_log_signal 的 FK 由建表迁移
# 显式命名为 fk_job_log_signal_host_id。硬编码任一名字都会在另一条路径
# 上 DROP 失败（空库 upgrade head 即 #510）。因此现存名一律经
# _find_host_fk() 从 pg_constraint 动态发现；canonical 名仅用于重建。
_FK_REBINDS = [
    ("device", "device_host_id_fkey", "CASCADE"),
    ("device_leases", "device_leases_host_id_fkey", "CASCADE"),
    ("device_log_event", "device_log_event_host_id_fkey", "CASCADE"),
    ("job_instance", "job_instance_host_id_fkey", "CASCADE"),
    ("job_log_signal", "job_log_signal_host_id_fkey", "CASCADE"),
    ("plan_run_host", "plan_run_host_host_id_fkey", "CASCADE"),
]

# Denormalized snapshot columns (no FK): updated explicitly after the parent.
_SNAPSHOT_REFERENCES = [
    ("plan_run_target_device", "host_id_snapshot"),
    ("plan_run_artifact", "host_id"),
]

_WHERE_LEGACY = "id ~ '^172-21-(8|9)-'"

# 查找 table 上「唯一指向 host.id 的单列 FK」的现存约束名（#510）。
# 表名来自本文件 _FK_REBINDS 硬编码清单（非用户输入），做标识符卫生校验后
# 内联——text():param::regclass 的绑定与 PG cast 组合存在解析歧义，故不走 bind。
def _find_host_fk(conn, table: str) -> str | None:
    import re

    if not re.fullmatch(r"[a-z_][a-z0-9_]*", table):
        raise RuntimeError(f"k8l9m0n1o2p3: invalid table identifier {table!r}")
    row = conn.execute(
        text(
            "SELECT conname FROM pg_constraint c "
            "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attname = 'host_id' "
            "WHERE c.contype = 'f' "
            f"AND c.conrelid = '{table}'::regclass "
            "AND c.confrelid = 'host'::regclass "
            "AND cardinality(c.conkey) = 1 "
            "AND c.conkey[1] = a.attnum"
        )
    ).fetchone()
    return row[0] if row else None


def upgrade() -> None:
    conn = op.get_bind()

    # 1) Rebuild the 6 FK constraints referencing host.id with ON UPDATE CASCADE.
    #    ON DELETE preserved from the current schema (dropping the constraint also
    #    drops the delete rule, so we re-declare it explicitly).
    #    现存名动态发现（#510）：存量库/空库链路的命名分叉在此收敛。
    for table, cname, on_delete in _FK_REBINDS:
        current = _find_host_fk(conn, table)
        if current is None:
            raise RuntimeError(
                f"k8l9m0n1o2p3: no single-column FK on {table}.host_id -> host.id "
                "found; schema drifted from both known naming paths"
            )
        op.drop_constraint(current, table, type_="foreignkey")
        op.create_foreign_key(
            cname,
            table,
            "host",
            ["host_id"],
            ["id"],
            ondelete=on_delete,
            onupdate="CASCADE",
        )

    # 2) Snapshot the old->new mapping BEFORE rewriting host.id, so the
    #    denormalized snapshot columns can be migrated after the parent.
    conn.execute(text("CREATE TEMP TABLE _host_id_map AS "
                      "SELECT id AS old_id, "
                      "       '172-21-15-' || split_part(ip, '.', 4) AS new_id "
                      f"FROM host WHERE {_WHERE_LEGACY}"))

    # 3) Renumber the parent in a single statement; all 6 FK child columns
    #    cascade in the same statement (no transient violation).
    conn.execute(
        text(
            f"""
            UPDATE host
            SET id = '172-21-15-' || split_part(ip, '.', 4)
            WHERE {_WHERE_LEGACY}
            """
        )
    )

    # 4) Explicitly rewrite the denormalized snapshot columns (no FK, hidden
    #    join contract to plan_run_host.host_id / historical reporting) using
    #    the pre-renumber mapping.
    for table, column in _SNAPSHOT_REFERENCES:
        conn.execute(
            text(
                f"""
                UPDATE {table} AS t
                SET {column} = m.new_id
                FROM _host_id_map AS m
                WHERE t.{column} = m.old_id
                """
            )
        )

    conn.execute(text("DROP TABLE _host_id_map"))


def downgrade() -> None:
    # No-op: rewriting host.id back would break live control-plane/agent rooms.
    pass
