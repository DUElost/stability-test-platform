import importlib.util
import logging
import os
import time
from typing import Dict, Optional

from sqlalchemy import create_engine, event
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from backend.core.env_source import resolve_database_url

logger = logging.getLogger(__name__)


def _record_pool_checkout(engine_label: str, elapsed: float, failure: Optional[str]) -> None:
    """写「借一条连接」的观测值。观测**绝不得**影响借还（与 `_refresh` 同一纪律）。"""
    try:
        from backend.core.metrics import (
            record_db_pool_checkout,
            record_db_pool_checkout_failure,
        )

        record_db_pool_checkout(engine_label, elapsed)
        if failure:
            record_db_pool_checkout_failure(engine_label, failure)
    except Exception:  # noqa: BLE001 — 观测层故障不外溢到取连接路径
        logger.debug("db_pool_checkout_metrics_failed label=%s", engine_label, exc_info=True)


def _instrument_pool_connect(pool, engine_label: str) -> None:
    """#703 第 3 面：给「借一条连接」补**等待/超时**观测。

    为什么包 ``Pool.connect()`` 而不是用 pool 事件：``checkout`` 事件在**已经拿到**连接之后
    才触发，排队等了多久、有没有超时它都不知道；``QueuePool`` 也没有公开的 waiters 计数
    （``_cond`` 是私有实现，跟着版本走）。``Pool.connect()`` 是公开方法，且
    ``Engine.connect()`` / Session / async 侧（``AsyncAdaptedQueuePool`` 继承同一入口）
    的每条取连接路径都经过它——一处包住即全覆盖。

    计时口径 = 排队等待 + 建连 + ``pool_pre_ping`` 往返，**不是纯排队时间**（指标 HELP 里
    写明）。超时按 ``sqlalchemy.exc.TimeoutError`` 判类（QueuePool 触顶抛的就是它，且它
    与内置 ``TimeoutError`` 无继承关系，不会误判），其余异常一律 ``error``。

    包装只装一次：同一条连接被计时两遍会让 p99 与超时计数同时失真。
    """
    if getattr(pool, "_stp_checkout_instrumented", False):
        return
    real_connect = pool.connect

    def _connect(*args, **kwargs):
        started = time.perf_counter()
        failure = None
        try:
            return real_connect(*args, **kwargs)
        except Exception as exc:  # 只分类，不改语义：原样抛出
            failure = classify_pool_checkout_failure(exc)
            raise
        finally:
            _record_pool_checkout(engine_label, time.perf_counter() - started, failure)

    pool.connect = _connect
    pool._stp_checkout_instrumented = True


def _attach_pool_metrics(engine, engine_label: str) -> None:
    """#703：QueuePool checkout/checkin → Prometheus Gauge；并包一层等待/超时观测。

    SQLite / NullPool 无 ``checkedout``/``overflow`` 语义，直接跳过（那条路径下
    「池耗尽」这个概念本身不成立，埋一个恒零的序列只会制造假绿）。
    """
    if is_sqlite_url(str(getattr(engine, "url", "") or "")):
        return
    pool = getattr(engine, "pool", None)
    if pool is None or not hasattr(pool, "checkedout"):
        return

    def _refresh(_conn=None, _rec=None) -> None:
        try:
            from backend.core.metrics import record_db_pool_status

            overflow = int(pool.overflow()) if hasattr(pool, "overflow") else 0
            record_db_pool_status(
                engine_label,
                checked_out=int(pool.checkedout()),
                overflow=max(0, overflow),
            )
        except Exception:  # noqa: BLE001 — 观测不得拖垮借还连接
            logger.debug("db_pool_metrics_refresh_failed label=%s", engine_label, exc_info=True)

    event.listen(pool, "checkout", lambda *a, **k: _refresh())
    event.listen(pool, "checkin", lambda *a, **k: _refresh())
    event.listen(pool, "close", lambda *a, **k: _refresh())
    event.listen(pool, "invalidate", lambda *a, **k: _refresh())

    _instrument_pool_connect(pool, engine_label)


# SQLSTATE 40P01 = deadlock_detected。asyncpg 与 psycopg 都在异常对象上暴露
# ``sqlstate``；没有该属性时回退到消息匹配（驱动版本差异的兜底）。
_DEADLOCK_SQLSTATE = "40P01"


def _is_deadlock(orig: object) -> bool:
    if orig is None:
        return False
    state = getattr(orig, "sqlstate", None)
    if state is not None:
        return state == _DEADLOCK_SQLSTATE
    return "deadlock detected" in str(orig).lower()


# SQLSTATE 53300 = too_many_connections。两个驱动都用同一串（实测：
# `asyncpg.exceptions.TooManyConnectionsError.sqlstate == psycopg.errors.TooManyConnections.sqlstate
# == "53300"`），所以按 sqlstate 判、按消息兜底（与 `_is_deadlock` 同一纪律）。
_SLOT_EXHAUSTED_SQLSTATE = "53300"
_SLOT_EXHAUSTED_MESSAGE_HINTS = (
    "too many connections",
    "remaining connection slots are reserved",
)


def _iter_exception_chain(orig: object, limit: int = 6):
    """异常链上的候选对象（sqlstate 可能被包在 `__cause__` / `__context__` 里）。

    为什么要走链而不是只看最外层：取连接失败时 SQLAlchemy 会把 DBAPI 异常包成自己的
    `DisconnectionError` / `DBAPIError`，**最外层没有 sqlstate**——只判最外层的结果就是
    "看着分类生效了，但生产上永远归到 error"（和 #1958 那轮"被通用 except 吞掉"同族）。
    """
    seen = 0
    node = orig
    while node is not None and seen < limit:
        yield node
        node = getattr(node, "__cause__", None) or getattr(node, "__context__", None)
        seen += 1


def _is_slot_exhausted(orig: object) -> bool:
    """PG 侧拒新建连接（槽位耗尽）——与「池内排队超时」是两种成因，处置也不同。"""
    for node in _iter_exception_chain(orig):
        if getattr(node, "sqlstate", None) == _SLOT_EXHAUSTED_SQLSTATE:
            return True
        text = str(node).lower()
        if any(hint in text for hint in _SLOT_EXHAUSTED_MESSAGE_HINTS):
            return True
    return False


def classify_pool_checkout_failure(exc: BaseException) -> str:
    """把「借不到连接」的异常归到三类之一（**指标值域，必须与 metrics 侧白名单一致**）。

    - `timeout`：池内排队到 `pool_timeout` 仍未拿到（池饱和）；
    - `slots_exhausted`：PostgreSQL 拒绝新建连接（槽位耗尽 / 保留槽挤占）；
    - `error`：其余 DBAPI/驱动失败。

    为什么非要有中间那类：#2959 的现场是 `TooManyConnectionsError` 一天 1211 次，
    全部混进 `kind="error"`——既有告警按 error 报就会把驱动抖动、网络重置和"数据库
    已经不接受连接"混成一条，而这三种的处置完全不同。**分不出来就等于没埋。**
    """
    if isinstance(exc, SQLAlchemyTimeoutError):
        return "timeout"
    if _is_slot_exhausted(exc):
        return "slots_exhausted"
    return "error"


# engine_label → 已注册的 handle_error 回调。仅用于「接线是否生效」的可断言性
# （``event.contains`` 需要函数引用，注册点不保留引用就无法证明线上确实接了）。
_db_error_handlers: Dict[str, object] = {}


def _attach_db_error_metrics(engine, engine_label: str):
    """#1958：数据库死锁 → Prometheus 计数 + 一条成因明确的 warning。

    ``handle_error`` 在 DBAPI 异常交给调用方**之前**触发，所以即使上层把异常
    吞进通用 ``except Exception``（回收器的逐候选失败分支就是如此），计数依然
    生效——这正是「只在服务端日志里可见」那类错误的收敛点。

    观测不得改变错误传播：本回调自身任何异常都只记 debug，且不吞原异常。
    返回注册的回调，便于测试直接驱动与断言（``event.contains`` 需要函数引用）。
    """
    def _on_handle_error(exception_context) -> None:
        try:
            if not _is_deadlock(
                getattr(exception_context, "original_exception", None)
            ):
                return
            from backend.core.metrics import record_db_deadlock

            record_db_deadlock(engine_label)
            logger.warning(
                "db_deadlock_detected engine=%s statement=%s",
                engine_label,
                str(getattr(exception_context, "statement", "") or "")[:200],
            )
        except Exception:  # noqa: BLE001 — 观测不得拖垮主流程或改变异常传播
            logger.debug(
                "db_error_metrics_failed label=%s", engine_label, exc_info=True,
            )

    event.listen(engine, "handle_error", _on_handle_error)
    _db_error_handlers[engine_label] = _on_handle_error
    return _on_handle_error

# 唯一解析入口是 env_source：ambient env → 仓库根 .env.backend，**绝无兜底默认**。
# 解析不到直接 RuntimeError——曾经这里的默认值是
# `postgresql+asyncpg://stp:password@localhost:5432/stp`（直接点名生产库，
# 只靠密码占位符侥幸没连上）。现在宁可拒绝启动，也不能猜。
DATABASE_URL, _ = resolve_database_url()


def is_sqlite_url(database_url: str) -> bool:
    return database_url.startswith("sqlite")


def normalize_sync_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgresql+psycopg2://"):
        return database_url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if database_url.startswith("sqlite+aiosqlite://"):
        return database_url.replace("sqlite+aiosqlite://", "sqlite://", 1)
    return database_url


def normalize_async_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+psycopg://"):
        return database_url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgresql+psycopg2://"):
        return database_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if database_url.startswith("sqlite://"):
        return database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return database_url


def _pool_env_int(name: str, default: int) -> int:
    """连接池容量 env 读取（#1516）。

    非法值/非正值回退默认：连接池容量误配（0、负数、拼写错误）比回退默认更危险
    ——`pool_size=0` 会让每次借连接都直接抛 `QueuePool limit ... reached`。
    """
    raw = (os.getenv(name) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


#: 共享 `_pool_capacity_kwargs()` 的引擎数（sync + async，同源容量）。ADR-0047 D1 的
#: 不变量按这个数乘：每加一个引擎就多一份池，预算校验与门禁都必须跟着走。
DB_POOL_ENGINES = 2


def _pool_capacity_kwargs() -> Dict[str, object]:
    """同步/异步引擎共用的池容量参数（同源 env 驱动，默认 20 / 20 / 1800 / 2s）.

    ADR-0047 v1.1（2026-09-23 裁决）：
    - 默认容量从 30/60 收到 **20/20**——两侧合计 80，落在 PG 可用槽
      （`max_connections − superuser_reserved − reserved`）以内；
    - `pool_timeout` 显式设 **2s**（此前是 SQLAlchemy 默认 30s）：排队到 2s 就快失败，
      调用方拿到 503 + `Retry-After`（`DB_OVERLOADED`）而不是等 30s 的「假卡顿」。
    预算是否成立不靠默认值保证：启动期由 `tools/dev/check_db_pool_budget.py` 硬校验。
    """
    return {
        "pool_size": _pool_env_int("STP_DB_POOL_SIZE", 20),
        "max_overflow": _pool_env_int("STP_DB_MAX_OVERFLOW", 20),
        "pool_recycle": _pool_env_int("STP_DB_POOL_RECYCLE", 1800),
        "pool_timeout": _pool_env_int("STP_DB_POOL_TIMEOUT", 2),
    }


def pool_capacity() -> Dict[str, int]:
    """池容量的**单一读数口**（ADR-0047 D1）：每引擎上限与应用侧总上限。

    门禁（`tools/dev/check_db_pool_budget.py`）与测试都从这里取数，避免「校验器自己
    另算一份」——两份算术就是下一次口径漂移的种子。env 非法值仍按 `_pool_env_int`
    回退默认（回退后若仍超出预算，门禁负责拒绝启动）。
    """
    kwargs = _pool_capacity_kwargs()
    per_engine = int(kwargs["pool_size"]) + int(kwargs["max_overflow"])
    return {
        "pool_size": int(kwargs["pool_size"]),
        "max_overflow": int(kwargs["max_overflow"]),
        "pool_timeout": int(kwargs["pool_timeout"]),
        "per_engine": per_engine,
        "engines": DB_POOL_ENGINES,
        "app_total": per_engine * DB_POOL_ENGINES,
    }


def db_application_name() -> str:
    """PG 连接的 ``application_name``（#2632）。

    事故复盘时 PG 日志只有 ``user@db``，无法回答「哪条链路/哪个用途连的」——2026-09-16
    起约 30 条「猜出来的 schema」的一次性 SQL 错误就因此无处归属（含超级用户）。带上这个
    名字后，`postgresql-*.log` 里每条连接与慢查询都能对上是控制面进程还是测试进程。

    用既有的 ``TESTING`` 区分（本仓既有约定），**不新增 env 键**；判据是"谁在用"，不是
    "连到哪个库"——库名护栏（``db_url_guard``）管后者，两者互补。
    """
    return "stability-tests" if os.getenv("TESTING") == "1" else "stability-backend"


def get_async_engine_kwargs(database_url: str) -> Dict[str, object]:
    if is_sqlite_url(database_url):
        return {}
    return {
        "pool_pre_ping": True,
        **_pool_capacity_kwargs(),
        # asyncpg 的 application_name 走 server_settings（不是顶层参数）
        "connect_args": {"server_settings": {"application_name": db_application_name()}},
    }


def get_sync_engine_kwargs(database_url: str) -> Dict[str, object]:
    """同步引擎 kwargs（#1516）。

    同步池原先只设 ``pool_pre_ping``，容量回退 SQLAlchemy ``QueuePool`` 默认
    ``5 + 10 = 15``；而共享该池的是 12 个 APScheduler 周期任务、SAQ 默认并发 10
    与 84 处 ``SessionLocal()`` 调用点。触顶后经
    ``leader_election`` 的 fail-closed 设计会连锁跳过全部 singleton 调度
    （Recycler / Reconciler / Watchdog），作业卡在 RUNNING/UNKNOWN 无自愈出口。
    异步池独立，带宽裕救不了同步侧，故两侧同源 env 驱动、默认同值。
    """
    kwargs: Dict[str, object] = {"future": True}
    if not is_sqlite_url(database_url):
        kwargs["pool_pre_ping"] = True
        kwargs.update(_pool_capacity_kwargs())
        # psycopg / psycopg2 都直接吃 libpq 的 application_name（#2632）
        kwargs["connect_args"] = {"application_name": db_application_name()}
    return kwargs


def has_aiosqlite() -> bool:
    return importlib.util.find_spec("aiosqlite") is not None


class _MissingAsyncConnectionContext:
    async def __aenter__(self):
        raise RuntimeError(
            "SQLite quick-test mode requires 'aiosqlite' for async database access. "
            "Install backend requirements or use TEST_DATABASE_URL with PostgreSQL."
        )

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _MissingAsyncEngine:
    def connect(self):
        return _MissingAsyncConnectionContext()

    async def dispose(self):
        return None


class _MissingAsyncSessionContext:
    async def __aenter__(self):
        raise RuntimeError(
            "Async DB session is unavailable in SQLite quick-test mode without 'aiosqlite'."
        )

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _MissingAsyncSessionFactory:
    def __call__(self, *args, **kwargs):
        return _MissingAsyncSessionContext()


_sync_url = normalize_sync_database_url(DATABASE_URL)
_async_url = normalize_async_database_url(DATABASE_URL)
_use_missing_async_runtime = (
    os.getenv("TESTING") == "1"
    and is_sqlite_url(DATABASE_URL)
    and not has_aiosqlite()
)

if _use_missing_async_runtime:
    # 本地 SQLite 快速回归允许导入应用，但显式禁止需要真实异步驱动的路径。
    async_engine = _MissingAsyncEngine()
    AsyncSessionLocal = _MissingAsyncSessionFactory()
else:
    async_engine = create_async_engine(_async_url, **get_async_engine_kwargs(_async_url))
    AsyncSessionLocal = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    _attach_pool_metrics(async_engine.sync_engine, "async")
    _attach_db_error_metrics(async_engine.sync_engine, "async")

# ── Sync engine (Alembic migrations + legacy API routes) ──
engine = create_engine(_sync_url, **get_sync_engine_kwargs(_sync_url))
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
_attach_pool_metrics(engine, "sync")
_attach_db_error_metrics(engine, "sync")

Base = declarative_base()


def get_db():
    """Sync session — used by all legacy routes that haven't migrated yet."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_async_db() -> AsyncSession:
    """Async session — used by new Phase 2+ routes."""
    async with AsyncSessionLocal() as session:
        yield session
