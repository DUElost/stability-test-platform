"""migrate legacy data to new schema

Revision ID: h6c7d8e9f0a1
Revises: g5b6c7d8e9f0
Create Date: 2026-03-26

Data migration: copy rows from legacy tables to new tables.
Idempotent — skips rows that already exist in the target tables.
Legacy tables are NOT dropped (that will be a future migration after
all application code has been fully migrated).

Mapping:
  hosts (int PK)        -> host (string PK = str(id))
  devices (int FK)      -> device (FK host.id = str(host_id))
  tools + categories    -> tool (category = string)
  log_artifacts         -> job_artifact (only for matching job_instance rows)
"""

from alembic import op
import sqlalchemy as sa

revision = "h6c7d8e9f0a1"
down_revision = "g5b6c7d8e9f0"
branch_labels = None
depends_on = None


def _table_exists(conn, table_name: str) -> bool:
    result = conn.execute(
        sa.text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.tables "
            "  WHERE table_schema = 'public' AND table_name = :tbl"
            ")"
        ),
        {"tbl": table_name},
    )
    return result.scalar()


def _count(conn, table: str) -> int:
    result = conn.execute(sa.text(f"SELECT COUNT(*) FROM {table}"))
    return result.scalar()


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Migrate hosts -> host ──────────────────────────────────────────
    if _table_exists(conn, "hosts") and _table_exists(conn, "host"):
        conn.execute(sa.text("""
            INSERT INTO host (id, hostname, ip_address, status, last_heartbeat, created_at,
                              name, ip)
            SELECT CAST(h.id AS VARCHAR(64)),
                   COALESCE(h.name, 'host-' || CAST(h.id AS TEXT)),
                   h.ip,
                   COALESCE(h.status, 'OFFLINE'),
                   h.last_heartbeat,
                   COALESCE(h.created_at, NOW()),
                   h.name,
                   h.ip
            FROM hosts h
            WHERE NOT EXISTS (
                SELECT 1 FROM host WHERE host.id = CAST(h.id AS VARCHAR(64))
            )
        """))

    # ── 2. Migrate devices -> device ──────────────────────────────────────
    if _table_exists(conn, "devices") and _table_exists(conn, "device"):
        conn.execute(sa.text("""
            INSERT INTO device (serial, host_id, model, status, created_at,
                                last_seen, adb_state, adb_connected,
                                battery_level, temperature, network_latency)
            SELECT d.serial,
                   CAST(d.host_id AS VARCHAR(64)),
                   d.model,
                   COALESCE(d.status, 'OFFLINE'),
                   COALESCE(d.created_at, NOW()),
                   d.last_seen,
                   d.adb_state,
                   d.adb_connected,
                   d.battery_level,
                   d.temperature,
                   d.network_latency
            FROM devices d
            WHERE d.serial IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM device WHERE device.serial = d.serial
              )
        """))

    # ── 3. Migrate tools + tool_categories -> tool ────────────────────────
    if _table_exists(conn, "tools") and _table_exists(conn, "tool"):
        has_categories = _table_exists(conn, "tool_categories")
        if has_categories:
            conn.execute(sa.text("""
                INSERT INTO tool (name, version, script_path, script_class,
                                  param_schema, is_active, description, category)
                SELECT t.name,
                       'legacy',
                       COALESCE(t.script_path, ''),
                       COALESCE(t.script_class, ''),
                       COALESCE(t.default_params, '{}')::jsonb,
                       COALESCE(t.enabled, true),
                       t.description,
                       tc.name
                FROM tools t
                LEFT JOIN tool_categories tc ON t.category_id = tc.id
                WHERE NOT EXISTS (
                    SELECT 1 FROM tool WHERE tool.name = t.name AND tool.version = 'legacy'
                )
            """))
        else:
            conn.execute(sa.text("""
                INSERT INTO tool (name, version, script_path, script_class,
                                  param_schema, is_active, description)
                SELECT t.name,
                       'legacy',
                       COALESCE(t.script_path, ''),
                       COALESCE(t.script_class, ''),
                       COALESCE(t.default_params, '{}')::jsonb,
                       COALESCE(t.enabled, true),
                       t.description
                FROM tools t
                WHERE NOT EXISTS (
                    SELECT 1 FROM tool WHERE tool.name = t.name AND tool.version = 'legacy'
                )
            """))

    # ── 4. Migrate log_artifacts -> job_artifact ──────────────────────────
    #    Only migrate artifacts whose run_id matches an existing job_instance.id
    if _table_exists(conn, "log_artifacts") and _table_exists(conn, "job_artifact"):
        conn.execute(sa.text("""
            INSERT INTO job_artifact (job_id, storage_uri, artifact_type,
                                      size_bytes, checksum, created_at)
            SELECT la.run_id,
                   la.storage_uri,
                   'log',
                   la.size_bytes,
                   la.checksum,
                   COALESCE(la.created_at, NOW())
            FROM log_artifacts la
            WHERE EXISTS (
                SELECT 1 FROM job_instance ji WHERE ji.id = la.run_id
            )
            AND NOT EXISTS (
                SELECT 1 FROM job_artifact ja
                WHERE ja.job_id = la.run_id AND ja.storage_uri = la.storage_uri
            )
        """))


def downgrade() -> None:
    pass
