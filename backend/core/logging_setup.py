"""Application logging configuration (#563).

Only the ``uvicorn`` loggers get handlers from uvicorn itself. Everything under
``backend.**`` was left handler-less, so ``logger.info(...)`` fell through to
:data:`logging.lastResort` — stderr, WARNING and above. In production that meant
every app-level INFO line was discarded: no ``schedule_registered``, no
``*_reconcile_done``, no ``watchdog_pass``. The only evidence that a periodic
sweep ran at all was whatever it changed in the database.

Handlers are attached to the ``backend`` logger alone (not the root logger) with
``propagate`` disabled, so uvicorn's own handlers keep working untouched and
nothing is emitted twice.

本模块另承载 **access log 降噪**（#3020）：uvicorn 的逐条 access 行是
``backend.log`` 剩余最大的单一行源，而其中 99.99% 是 200、87% 来自 7 条 agent
内部轮询路径——逐条留存没有埋点价值，聚合视图由
``stability_api_requests_total{endpoint,method,status_code}`` 承接。
"""

from __future__ import annotations

import logging
import os
import re
import sys

APP_LOGGER_NAME = "backend"
ACCESS_LOGGER_NAME = "uvicorn.access"
DEFAULT_LOG_LEVEL = "INFO"

_DATEFMT = "%Y-%m-%d %H:%M:%S"
_FMT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"
_HANDLER_NAME = "stp_app_stdout"

#: 逃生阀：置 `1/true/yes/on` 时 access log 一条不丢（现场排查「这次请求到底到没到」
#: 时用）。默认关闭 = 降噪生效。
#:
#: 注意：读取处必须是**字面量** —— `tools/dev/env_inventory.py` 只扫
#: `os.getenv("X")` 这类字面量形态，用模块常量包一层会让该键从环境变量清单里消失
#: （#3020 实现时的实测：写成 `os.getenv(ACCESS_LOG_FULL_ENV)` 时清单仍是 244 项、
#: 门禁照过，键对运维不可见）。
ACCESS_LOG_FULL_ENV = "STP_ACCESS_LOG_FULL"

#: 高频**内部轮询**端点（#3020 实测占 access 行：patrol-heartbeat 39.4% /
#: claim 14.7% / heartbeat 12.2% / extend_lock 7.5% / coordinator-heartbeat 6.1% /
#: recovery/sync 4.3% / leases extend-batch 3.0%）。只丢这些路径的 **2xx**：
#: 非 2xx 恒保留，排查面不受影响。判定用剥掉 query 的 path，id 段按 `[^/]+` 通配，
#: **末尾锚定**（`/api/v1/heartbeat` 不得吃掉 `/api/v1/heartbeat-detail`）。
ACCESS_LOG_NOISE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"/api/v1/agent/jobs/[^/]+/patrol-heartbeat$",
        r"/api/v1/agent/jobs/[^/]+/extend_lock$",
        r"/api/v1/agent/jobs/claim$",
        r"/api/v1/agent/coordinator-heartbeat$",
        r"/api/v1/agent/recovery/sync$",
        r"/api/v1/agent/leases/extend-batch$",
        r"/api/v1/heartbeat$",
    )
)


def access_log_full() -> bool:
    """逃生阀是否打开（`STP_ACCESS_LOG_FULL`；字面量读取，见上方说明）。"""
    return (os.getenv("STP_ACCESS_LOG_FULL") or "").strip().lower() in {"1", "true", "yes", "on"}


def is_access_log_noise(path: str, status_code: int) -> bool:
    """该 access 行是否属于「高频内部轮询的**成功**行」（纯函数，便于自证）。

    只丢 2xx：`1xx/3xx` 与非 2xx 一样**保留**。判据写成 `not (200 <= sc < 300)`
    而非 `sc >= 400`——后者会把重定向也当成功吞掉，与上面声明的
    「非 2xx 恒保留」不一致（#3100）。
    """
    if not (200 <= status_code < 300):
        return False
    clean = path.split("?", 1)[0]
    return any(pattern.search(clean) for pattern in ACCESS_LOG_NOISE_PATTERNS)


class AccessLogNoiseFilter(logging.Filter):
    """丢掉 `is_access_log_noise` 判定的 access 行（#3020）。

    判据取自 uvicorn 自己的 access 记录形状：`record.args` 为
    `(client_addr, method, full_path, http_version, status_code)`。形状不符
    （uvicorn 改版、或记录被别的库改写）时**放行**——宁可多记一行，
    不可静默丢证据。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if access_log_full():
            return True
        args = record.args
        if not (isinstance(args, tuple) and len(args) >= 5):
            return True
        try:
            status_code = int(args[4])
        except (TypeError, ValueError):
            return True
        return not is_access_log_noise(str(args[2]), status_code)


def install_access_log_filter() -> logging.Filter:
    """把噪声过滤器挂到 `uvicorn.access`（幂等）。

    挂**logger** 而非 handler：uvicorn 的 `dictConfig`（`LOGGING_CONFIG`）重建
    handler，但该 logger 的配置项里没有 `filters`，已有 filter 不会被清掉
    （`backend/tests/core/test_logging_setup.py` 有实测钉子）。调用点在
    `backend/main.py` 的模块级——uvicorn 先 `configure_logging()` 再导入 app，
    故此时 access logger 已成形。
    """
    access_logger = logging.getLogger(ACCESS_LOGGER_NAME)
    installed = next(
        (f for f in access_logger.filters if isinstance(f, AccessLogNoiseFilter)), None
    )
    if installed is not None:
        return installed

    installed = AccessLogNoiseFilter()
    access_logger.addFilter(installed)
    logging.getLogger(APP_LOGGER_NAME).info(
        "access_log_noise_filter active patterns=%d（%s=1 可关闭）",
        len(ACCESS_LOG_NOISE_PATTERNS),
        ACCESS_LOG_FULL_ENV,
    )
    return installed


def resolve_log_level() -> int:
    """Level for the ``backend`` logger; ``STP_LOG_LEVEL`` (default ``INFO``)."""
    raw = (os.getenv("STP_LOG_LEVEL") or DEFAULT_LOG_LEVEL).strip().upper()
    level = logging.getLevelName(raw)
    # getLevelName returns the "Level %s" placeholder string for unknown names.
    return level if isinstance(level, int) else logging.INFO


def configure_logging() -> logging.Logger:
    """Attach a stdout handler to the ``backend`` logger. Idempotent."""
    app_logger = logging.getLogger(APP_LOGGER_NAME)
    app_logger.setLevel(resolve_log_level())

    if any(h.get_name() == _HANDLER_NAME for h in app_logger.handlers):
        return app_logger

    handler = logging.StreamHandler(sys.stdout)
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(logging.Formatter(_FMT, datefmt=_DATEFMT))
    app_logger.addHandler(handler)
    # Keep records off the root logger: uvicorn already owns stdout formatting,
    # and root's lastResort would duplicate WARNING+ onto stderr.
    app_logger.propagate = False
    return app_logger
