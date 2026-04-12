from backend.core.database import (
    get_async_engine_kwargs,
    normalize_async_database_url,
    normalize_sync_database_url,
)


def test_normalize_sync_database_url_for_postgres_drivers():
    assert (
        normalize_sync_database_url("postgresql+asyncpg://user:pass@localhost:5432/stp")
        == "postgresql+psycopg://user:pass@localhost:5432/stp"
    )
    assert (
        normalize_sync_database_url("postgresql://user:pass@localhost:5432/stp")
        == "postgresql+psycopg://user:pass@localhost:5432/stp"
    )


def test_normalize_async_database_url_for_postgres_drivers():
    assert (
        normalize_async_database_url("postgresql+psycopg://user:pass@localhost:5432/stp")
        == "postgresql+asyncpg://user:pass@localhost:5432/stp"
    )
    assert (
        normalize_async_database_url("postgresql://user:pass@localhost:5432/stp")
        == "postgresql+asyncpg://user:pass@localhost:5432/stp"
    )


def test_normalize_database_urls_for_sqlite():
    assert normalize_sync_database_url("sqlite+aiosqlite:///:memory:") == "sqlite:///:memory:"
    assert normalize_async_database_url("sqlite:///:memory:") == "sqlite+aiosqlite:///:memory:"


def test_get_async_engine_kwargs_for_postgres_keeps_pool_settings():
    assert get_async_engine_kwargs("postgresql+asyncpg://user:pass@localhost:5432/stp") == {
        "pool_pre_ping": True,
        "pool_size": 10,
        "max_overflow": 20,
        "pool_recycle": 3600,
    }


def test_get_async_engine_kwargs_for_sqlite_omits_queue_pool_settings():
    assert get_async_engine_kwargs("sqlite+aiosqlite:///:memory:") == {}
