"""B6 U8 empty isolated PG upgrade→downgrade→upgrade and nullable schema."""
from sqlalchemy import create_engine, inspect
from testcontainers.postgres import PostgresContainer
from tests.test_alembic_upgrade import _alembic, _normalize_database_url, _head_revision


def test_disarm_guard_upgrade_downgrade_upgrade_nullable_jsonb():
    assert _head_revision() == "u8b6d3752a01"
    with PostgresContainer("postgres:16") as postgres:
        url = _normalize_database_url(postgres.get_connection_url())
        env = {"DATABASE_URL": url, "TESTING": "1", "JWT_SECRET_KEY": "b6-isolated-test-only"}
        engine = create_engine(url)
        try:
            for command, target, present in [
                ("upgrade", "head", True), ("downgrade", "-1", False), ("upgrade", "head", True),
            ]:
                result = _alembic(env, command, target)
                assert result.returncode == 0, result.stderr
                columns = {c["name"]: c for c in inspect(engine).get_columns("job_instance")}
                assert ("disarm_guard" in columns) is present
                if present:
                    assert columns["disarm_guard"]["nullable"]
                    assert str(columns["disarm_guard"]["type"]) == "JSONB"
        finally:
            engine.dispose()
