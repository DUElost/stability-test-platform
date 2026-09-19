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
        # #2632：asyncpg 的 application_name 走 server_settings（不是顶层参数）；
        # 套件内 TESTING=1（conftest），故期望值是 tests 名——名字映射由
        # backend/tests/core/test_db_application_name.py 钉住。
        "connect_args": {"server_settings": {"application_name": "stability-tests"}},
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
        # #2632：psycopg / psycopg2 直接吃顶层 application_name。
        "connect_args": {"application_name": "stability-tests"},
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



# ── #703 第 3 面：池 checkout 的**等待/超时**观测（此前只有 checked_out/overflow 两个状态）──
def _probe_registry():
    """读默认注册表里的样本；缺序列按 0 计（首次写入前 prometheus 不建子序列）。"""
    from prometheus_client import REGISTRY

    def get(name: str, labels: dict | None = None) -> float:
        return REGISTRY.get_sample_value(name, labels or {}) or 0.0

    return get


def _make_probe_pool(*, size: int = 1, overflow: int = 0, timeout: float = 0.05):
    """一条**不连真实库**的 QueuePool：creator 给 sqlite3 连接，只为让排队/超时成立。

    为什么不走 testcontainers PG：本用例要的是「池满了会怎样」，与后端方言无关；
    用真库反而要让 timeout=0.05 与容器往返争时间，测到的可能是网络而不是排队。
    """
    import sqlite3

    from sqlalchemy import create_engine
    from sqlalchemy.pool import QueuePool

    def _creator():
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE IF NOT EXISTS probe (x int)")
        return conn

    return create_engine(
        "sqlite+pysqlite://",
        creator=_creator,
        poolclass=QueuePool,
        pool_size=size,
        max_overflow=overflow,
        pool_timeout=timeout,
        pool_pre_ping=False,
    )


def test_pool_checkout_records_wait_and_timeout():
    """池触顶：等待时长要进直方图、超时要单独成一条 kind=timeout 的序列。

    这是 #703 现场那一条（`QueuePool limit of size 30 overflow 60`）在指标上唯一
    留得下痕的形式——两个状态 gauge 在「满且忙」与「满且排队」之间是同形的。
    """
    import pytest
    from sqlalchemy.exc import TimeoutError as SATimeoutError

    from backend.core.database import _instrument_pool_connect

    get = _probe_registry()
    engine = _make_probe_pool(size=1, overflow=0, timeout=0.05)
    _instrument_pool_connect(engine.pool, "probe")

    count_before = get("stability_db_pool_checkout_seconds_count", {"engine": "probe"})
    sum_before = get("stability_db_pool_checkout_seconds_sum", {"engine": "probe"})
    timeout_before = get(
        "stability_db_pool_checkout_failures_total", {"engine": "probe", "kind": "timeout"}
    )
    error_before = get(
        "stability_db_pool_checkout_failures_total", {"engine": "probe", "kind": "error"}
    )

    held = engine.pool.connect()
    try:
        with pytest.raises(SATimeoutError):
            engine.pool.connect()  # 第二条借不到 → 排队到 pool_timeout 后抛
    finally:
        held.close()
        engine.dispose()

    assert get("stability_db_pool_checkout_seconds_count", {"engine": "probe"}) == count_before + 2, (
        "成功与超时都必须各留一次观测：只记成功就看不见『等了多久才失败』"
    )
    # 关键判据：超时那一次的**等待时长进了直方图**（≥ pool_timeout），
    # 恢复前这里恒为 0 —— 那就是「池耗尽只能翻日志」的原状。
    assert get("stability_db_pool_checkout_seconds_sum", {"engine": "probe"}) - sum_before >= 0.05, (
        "排队等待没被计时：这条序列退化成『只数成功次数』，#703 的前兆信号仍然不存在"
    )
    assert get(
        "stability_db_pool_checkout_failures_total", {"engine": "probe", "kind": "timeout"}
    ) == timeout_before + 1
    assert get(
        "stability_db_pool_checkout_failures_total", {"engine": "probe", "kind": "error"}
    ) == error_before, "超时不得混进 error——两类成因的处置完全不同"


def test_pool_checkout_classifies_non_timeout_as_error():
    """非 TimeoutError 的取连接失败 → kind=error（分类只看异常类型，不依赖池实现）。"""
    import pytest

    from backend.core.database import _instrument_pool_connect

    class _Boom:
        def connect(self, *args, **kwargs):
            raise RuntimeError("driver down")

    boom = _Boom()
    _instrument_pool_connect(boom, "probe")
    get = _probe_registry()
    error_before = get(
        "stability_db_pool_checkout_failures_total", {"engine": "probe", "kind": "error"}
    )
    timeout_before = get(
        "stability_db_pool_checkout_failures_total", {"engine": "probe", "kind": "timeout"}
    )
    with pytest.raises(RuntimeError):
        boom.connect()
    assert get(
        "stability_db_pool_checkout_failures_total", {"engine": "probe", "kind": "error"}
    ) == error_before + 1
    assert get(
        "stability_db_pool_checkout_failures_total", {"engine": "probe", "kind": "timeout"}
    ) == timeout_before


def test_pool_checkout_instrumentation_is_installed_once():
    """重复安装不得叠加计时：否则同一次借连接记两遍，p99 与超时数都会失真。"""
    from backend.core.database import _instrument_pool_connect

    engine = _make_probe_pool(size=2, overflow=0, timeout=5)
    _instrument_pool_connect(engine.pool, "probe")
    _instrument_pool_connect(engine.pool, "probe")
    get = _probe_registry()
    before = get("stability_db_pool_checkout_seconds_count", {"engine": "probe"})
    conn = engine.pool.connect()
    conn.close()
    engine.dispose()
    assert get("stability_db_pool_checkout_seconds_count", {"engine": "probe"}) == before + 1


def test_attach_pool_metrics_installs_checkout_probe_on_pg_engine():
    """接线在场断言：生产路径（`_attach_pool_metrics`）必须真的把计时装到池上。

    单独测 `_instrument_pool_connect` 不够——它被谁调用是 AST 追不到的那一跳
    （与 `_MANUAL_WIRED_METRICS` 同一教训）。这里用**不连库**的 PG 引擎走真实入口。
    """
    from sqlalchemy import create_engine

    from backend.core.database import _attach_pool_metrics

    sqlite = create_engine("sqlite:///:memory:")
    _attach_pool_metrics(sqlite, "sync")  # SQLite 不装（池耗尽语义不成立）
    assert getattr(sqlite.pool, "_stp_checkout_instrumented", False) is False, (
        "SQLite 也装探针 = 给不存在的『池耗尽』造一条恒零序列（假绿）"
    )

    pgish = create_engine(
        "postgresql+psycopg://user:pass@127.0.0.1:1/stp",
        pool_pre_ping=False,
        pool_size=1,
        max_overflow=0,
    )
    try:
        assert getattr(pgish.pool, "_stp_checkout_instrumented", False) is False
        _attach_pool_metrics(pgish, "sync")
        assert getattr(pgish.pool, "_stp_checkout_instrumented", False) is True
    finally:
        pgish.dispose()
