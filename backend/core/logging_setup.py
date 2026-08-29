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
"""

from __future__ import annotations

import logging
import os
import sys

APP_LOGGER_NAME = "backend"
DEFAULT_LOG_LEVEL = "INFO"

_DATEFMT = "%Y-%m-%d %H:%M:%S"
_FMT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"
_HANDLER_NAME = "stp_app_stdout"


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
