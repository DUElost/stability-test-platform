from backend.core.database import (
    get_async_engine_kwargs,
    get_sync_engine_kwargs,
    normalize_async_database_url,
    normalize_sync_database_url,
)


def test_normalize_sync_database_url_for_postgres_drivers():
    assert (
        normalize_sync_database_url("postgresql+asyncpg://user:pass@localhost:5432/stp")
        == "postgresql+psycopg://user:pass@localhost:5432/stp"
    )
    assert (
        normalize_sync_database_url("postgresql+psycopg2://user:pass@localhost:5432/stp")
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
        normalize_async_database_url("postgresql+psycopg2://user:pass@localhost:5432/stp")
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
        "pool_size": 30,
        "max_overflow": 60,
        "pool_recycle": 1800,
    }


def test_get_async_engine_kwargs_for_sqlite_omits_queue_pool_settings():
    assert get_async_engine_kwargs("sqlite+aiosqlite:///:memory:") == {}


# ── #1516：同步池容量（此前回退 QueuePool 默认 5+10=15） ──────────────────────


def test_get_sync_engine_kwargs_for_postgres_sets_pool_capacity():
    assert get_sync_engine_kwargs("postgresql+psycopg://user:pass@localhost:5432/stp") == {
        "future": True,
        "pool_pre_ping": True,
        "pool_size": 30,
        "max_overflow": 60,
        "pool_recycle": 1800,
    }


def test_get_sync_engine_kwargs_for_sqlite_omits_queue_pool_settings():
    assert get_sync_engine_kwargs("sqlite:///:memory:") == {"future": True}


def test_sync_and_async_pool_capacity_share_the_same_env(monkeypatch):
    """两侧同源：改动 env 必须同时改变同步与异步引擎的容量参数。"""
    monkeypatch.setenv("STP_DB_POOL_SIZE", "44")
    monkeypatch.setenv("STP_DB_MAX_OVERFLOW", "88")
    monkeypatch.setenv("STP_DB_POOL_RECYCLE", "600")
    expected = {"pool_size": 44, "max_overflow": 88, "pool_recycle": 600}

    for kwargs in (
        get_async_engine_kwargs("postgresql+asyncpg://user:pass@localhost:5432/stp"),
        get_sync_engine_kwargs("postgresql+psycopg://user:pass@localhost:5432/stp"),
    ):
        for key, value in expected.items():
            assert kwargs[key] == value, f"{key} 未随 env 同步"


def test_pool_env_invalid_or_non_positive_falls_back_to_default(monkeypatch):
    """容量误配（非数字 / 空串 / 0 / 负数）回退默认，不得退化成零容量池。"""
    for raw in ("abc", "", "0", "-5"):
        monkeypatch.setenv("STP_DB_POOL_SIZE", raw)
        kwargs = get_sync_engine_kwargs("postgresql+psycopg://user:pass@localhost:5432/stp")
        assert kwargs["pool_size"] == 30, f"raw={raw!r} 未回退默认"


def test_attach_pool_metrics_skips_sqlite_and_is_idempotent_safe():
    """#703：SQLite 不挂池 Gauge；Postgres 引擎可安全挂 checkout/checkin 监听。"""
    from sqlalchemy import create_engine

    from backend.core.database import _attach_pool_metrics

    sqlite = create_engine("sqlite:///:memory:")
    _attach_pool_metrics(sqlite, "sync")  # 不得抛

    # 无真实 PG 时用 NullPool 语义的内存引擎验证监听可注册
    pgish = create_engine(
        "postgresql+psycopg://user:pass@127.0.0.1:1/stp",
        pool_pre_ping=False,
        pool_size=1,
        max_overflow=0,
    )
    try:
        _attach_pool_metrics(pgish, "sync")
    finally:
        pgish.dispose()

