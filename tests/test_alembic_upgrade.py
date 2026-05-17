from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"


def _normalize_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)


def _prepare_pre_status_enum_schema(database_url: str) -> None:
    engine = create_engine(database_url, future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('l2m3n4o5p6q7')"))

        conn.execute(
            text(
                """
                CREATE TABLE plan_run (
                    id INTEGER PRIMARY KEY,
                    status VARCHAR(32) NOT NULL DEFAULT 'RUNNING'
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE job_instance (
                    id INTEGER PRIMARY KEY,
                    plan_run_id INTEGER,
                    device_id INTEGER,
                    status VARCHAR(32) NOT NULL DEFAULT 'PENDING'
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE UNIQUE INDEX uq_job_active_per_device
                    ON job_instance (device_id)
                 WHERE status IN ('PENDING', 'RUNNING', 'UNKNOWN')
                """
            )
        )
    engine.dispose()


def test_alembic_upgrade_head_succeeds_from_pre_status_enum_schema():
    with PostgresContainer("postgres:16") as postgres:
        env = os.environ.copy()
        env["DATABASE_URL"] = _normalize_database_url(postgres.get_connection_url())
        _prepare_pre_status_enum_schema(env["DATABASE_URL"])

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                str(BACKEND_DIR / "alembic.ini"),
                "upgrade",
                "head",
            ],
            cwd=BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    assert result.returncode == 0, result.stderr
