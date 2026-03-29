"""
Dual-Track Schema Validation Script (read-only).

Validates the database schema state after ADR-0008 incremental migrations:
1. All new tables exist with correct column types
2. Legacy tables coexistence (expected during dual-track; flagged as INFO)
3. Data migration completeness (new tables have rows if legacy tables do)

Usage:
    python backend/tests/e2e/validate_phase_c_state.py

Environment:
    STP_DATABASE_URL or DATABASE_URL
"""

from __future__ import annotations

import os
import sys
from typing import Optional

from sqlalchemy import create_engine, text


NEW_TABLES = [
    "host", "device", "workflow_definition", "task_template",
    "workflow_run", "job_instance", "step_trace", "tool",
    "job_artifact", "action_template",
]

LEGACY_TABLES = [
    "hosts", "devices", "tasks", "task_runs", "run_steps",
    "tools", "tool_categories", "log_artifacts",
]

STRING_TYPES = {"character varying", "text"}


def _resolve_database_url() -> Optional[str]:
    return os.getenv("STP_DATABASE_URL") or os.getenv("DATABASE_URL")


def _to_sync_url(url: str) -> str:
    return (
        url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
        .replace("postgresql://", "postgresql+psycopg://")
    )


def _table_exists(conn, table_name: str) -> bool:
    stmt = text(
        "SELECT EXISTS ("
        "  SELECT 1 FROM information_schema.tables "
        "  WHERE table_schema = 'public' AND table_name = :tbl"
        ")"
    )
    return bool(conn.execute(stmt, {"tbl": table_name}).scalar())


def _column_data_type(conn, table_name: str, column_name: str) -> Optional[str]:
    stmt = text(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = :tbl AND column_name = :col"
    )
    row = conn.execute(stmt, {"tbl": table_name, "col": column_name}).first()
    return row[0] if row else None


def _row_count(conn, table_name: str) -> int:
    return conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()


def main() -> int:
    raw_url = _resolve_database_url()
    if not raw_url:
        print("[FAIL] Missing database URL: set STP_DATABASE_URL or DATABASE_URL")
        return 2

    db_url = _to_sync_url(raw_url)
    engine = create_engine(db_url, future=True)
    failures = []
    infos = []

    try:
        with engine.connect() as conn:
            # 1) New tables must exist
            for table in NEW_TABLES:
                if _table_exists(conn, table):
                    infos.append(f"[OK] new table exists: {table}")
                else:
                    failures.append(f"[FAIL] missing new table: {table}")

            # 2) Key column types
            host_id_type = _column_data_type(conn, "host", "id")
            if host_id_type in STRING_TYPES:
                infos.append(f"[OK] host.id type: {host_id_type}")
            elif host_id_type:
                failures.append(f"[FAIL] host.id type should be string, got: {host_id_type}")

            device_host_id = _column_data_type(conn, "device", "host_id")
            if device_host_id in STRING_TYPES:
                infos.append(f"[OK] device.host_id type: {device_host_id}")
            elif device_host_id:
                failures.append(f"[FAIL] device.host_id type should be string, got: {device_host_id}")

            # job_instance post-completion columns
            for col in ("report_json", "jira_draft_json", "post_processed_at"):
                dt = _column_data_type(conn, "job_instance", col)
                if dt:
                    infos.append(f"[OK] job_instance.{col} exists ({dt})")
                else:
                    failures.append(f"[FAIL] job_instance.{col} missing")

            # tool.category
            cat_type = _column_data_type(conn, "tool", "category")
            if cat_type:
                infos.append(f"[OK] tool.category exists ({cat_type})")
            else:
                failures.append("[FAIL] tool.category missing")

            # 3) Legacy table coexistence (INFO, not failure)
            for table in LEGACY_TABLES:
                if _table_exists(conn, table):
                    infos.append(f"[INFO] legacy table still exists: {table} (expected during dual-track)")
                else:
                    infos.append(f"[INFO] legacy table already dropped: {table}")

            # 4) Data migration check
            if _table_exists(conn, "host") and _table_exists(conn, "hosts"):
                old_count = _row_count(conn, "hosts")
                new_count = _row_count(conn, "host")
                if old_count > 0 and new_count == 0:
                    failures.append(f"[FAIL] hosts has {old_count} rows but host has 0 — data migration incomplete")
                else:
                    infos.append(f"[OK] host rows: {new_count} (legacy hosts: {old_count})")

    except Exception as exc:
        print(f"[FAIL] validation error: {exc}")
        return 2
    finally:
        engine.dispose()

    for line in infos:
        print(line)
    for line in failures:
        print(line)

    if failures:
        print(f"\n[SUMMARY] FAIL ({len(failures)} issue(s))")
        return 2

    print(f"\n[SUMMARY] PASS ({len(infos)} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
