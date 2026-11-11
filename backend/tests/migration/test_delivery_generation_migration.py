"""B8 G5 add-column migration round-trip (#3929).

Verifies upgrade -> downgrade -> upgrade on an isolated PostgreSQL 16 database.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect
from testcontainers.postgres import PostgresContainer

BACKEND_DIR = Path(__file__).resolve().parents[2]
PREVIOUS_REVISION = "b83875a11c02"


def _normalize_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)


def _alembic(database_url: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": database_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _columns(database_url: str, table: str) -> set[str]:
    engine = create_engine(database_url)
    try:
        return {column["name"] for column in inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


def test_delivery_generation_migration_roundtrip():
    with PostgresContainer("postgres:16") as postgres:
        database_url = _normalize_database_url(postgres.get_connection_url())

        up = _alembic(database_url, "upgrade", "head")
        assert up.returncode == 0, up.stderr
        assert "merge_generation" in _columns(database_url, "scan_round")
        assert "basis_rounds" in _columns(database_url, "delivery_version")

        down = _alembic(database_url, "downgrade", PREVIOUS_REVISION)
        assert down.returncode == 0, down.stderr
        assert "merge_generation" not in _columns(database_url, "scan_round")
        assert "basis_rounds" not in _columns(database_url, "delivery_version")

        again = _alembic(database_url, "upgrade", "head")
        assert again.returncode == 0, again.stderr
        assert "merge_generation" in _columns(database_url, "scan_round")
        assert "basis_rounds" in _columns(database_url, "delivery_version")
