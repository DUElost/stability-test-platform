"""#3875: one additive migration, with real PG upgrade/downgrade and retained rows."""

import os

from sqlalchemy import create_engine, inspect, text
from testcontainers.postgres import PostgresContainer

from tests.test_alembic_upgrade import _alembic, _normalize_database_url


def test_shadow_settled_migration_preserves_existing_partition():
    with PostgresContainer("postgres:17") as postgres:
        url = _normalize_database_url(postgres.get_connection_url())
        env = {**os.environ, "DATABASE_URL": url}
        before = _alembic(env, "upgrade", "b8g1c4a7d2e9")
        assert before.returncode == 0, before.stderr
        engine = create_engine(url)
        try:
            with engine.begin() as db:
                db.execute(text("INSERT INTO partition_mode(plan_run_id, platform, mode, written_at) "
                    "VALUES (3875, 'mtk', 'CURRENT', now())"))
            upgraded = _alembic(env, "upgrade", "head")
            assert upgraded.returncode == 0, upgraded.stderr
            with engine.begin() as db:
                assert db.execute(text("SELECT shadow_settled_at FROM partition_mode WHERE plan_run_id=3875")).scalar_one() is None
                db.execute(text("UPDATE partition_mode SET shadow_settled_at=now() WHERE plan_run_id=3875"))
            downgraded = _alembic(env, "downgrade", "b8g1c4a7d2e9")
            assert downgraded.returncode == 0, downgraded.stderr
            assert "shadow_settled_at" not in {c["name"] for c in inspect(engine).get_columns("partition_mode")}
            with engine.connect() as db:
                assert db.execute(text("SELECT mode FROM partition_mode WHERE plan_run_id=3875")).scalar_one() == "CURRENT"
        finally:
            engine.dispose()
