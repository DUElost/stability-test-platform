"""#563 — application loggers must have a handler in production.

Without ``configure_logging()`` the ``backend.**`` loggers are handler-less and
``logger.info()`` is swallowed by ``logging.lastResort`` (stderr, WARNING+).
"""

from __future__ import annotations

import logging

import pytest

from backend.core.logging_setup import (
    APP_LOGGER_NAME,
    _HANDLER_NAME,
    configure_logging,
    resolve_log_level,
)


@pytest.fixture
def backend_logger_restored():
    """Snapshot/restore the ``backend`` logger so tests cannot leak state."""
    logger = logging.getLogger(APP_LOGGER_NAME)
    saved_handlers = list(logger.handlers)
    saved_level = logger.level
    saved_propagate = logger.propagate
    yield logger
    logger.handlers[:] = saved_handlers
    logger.setLevel(saved_level)
    logger.propagate = saved_propagate


def test_configure_attaches_stdout_handler(backend_logger_restored):
    logger = backend_logger_restored
    logger.handlers[:] = []

    configure_logging()

    assert [h.get_name() for h in logger.handlers] == [_HANDLER_NAME]
    assert logger.propagate is False, "records must not reach root/uvicorn handlers"


def test_configure_is_idempotent(backend_logger_restored):
    logger = backend_logger_restored
    logger.handlers[:] = []

    configure_logging()
    configure_logging()

    assert len(logger.handlers) == 1, "repeat calls must not duplicate handlers"


def test_child_logger_emits_info_to_stdout(backend_logger_restored, capsys):
    logger = backend_logger_restored
    logger.handlers[:] = []
    configure_logging()

    logging.getLogger("backend.scheduler.signal_link_reconciler").info(
        "signal_link_reconcile_done %s", {"scanned": 3, "linked": 2},
    )

    captured = capsys.readouterr()
    assert "signal_link_reconcile_done" in captured.out
    assert "backend.scheduler.signal_link_reconciler" in captured.out


def test_child_logger_warning_not_duplicated_to_stderr(
    backend_logger_restored, capsys,
):
    """propagate=False: WARNING must appear once on stdout, never on stderr."""
    logger = backend_logger_restored
    logger.handlers[:] = []
    configure_logging()

    logging.getLogger("backend.scheduler.app_scheduler").warning("drift detected")

    captured = capsys.readouterr()
    assert captured.out.count("drift detected") == 1
    assert "drift detected" not in captured.err


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("DEBUG", logging.DEBUG),
        ("WARNING", logging.WARNING),
        ("", logging.INFO),
        ("NOT_A_LEVEL", logging.INFO),
    ],
)
def test_resolve_log_level(raw, expected, monkeypatch):
    if raw:
        monkeypatch.setenv("STP_LOG_LEVEL", raw)
    else:
        monkeypatch.delenv("STP_LOG_LEVEL", raising=False)

    assert resolve_log_level() == expected
