"""#563 — application loggers must have a handler in production.

Without ``configure_logging()`` the ``backend.**`` loggers are handler-less and
``logger.info()`` is swallowed by ``logging.lastResort`` (stderr, WARNING+).
"""

from __future__ import annotations

import logging
import logging.config
import pathlib

import pytest

from backend.core.logging_setup import (
    ACCESS_LOG_FULL_ENV,
    ACCESS_LOGGER_NAME,
    APP_LOGGER_NAME,
    _HANDLER_NAME,
    AccessLogNoiseFilter,
    configure_logging,
    install_access_log_filter,
    is_access_log_noise,
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


# ── #3020：access log 降噪（丢高频内部轮询的 2xx，非 2xx 恒保留）──────────────

NOISE_PATHS = [
    "/api/v1/agent/jobs/1234/patrol-heartbeat",
    "/api/v1/agent/jobs/1234/extend_lock",
    "/api/v1/agent/jobs/claim",
    "/api/v1/agent/coordinator-heartbeat",
    "/api/v1/agent/recovery/sync",
    "/api/v1/agent/leases/extend-batch",
    "/api/v1/heartbeat",
]

KEPT_PATHS = [
    "/api/v1/plan-runs/12",
    "/api/v1/devices?skip=0&limit=1200",
    "/api/v1/agent/jobs/1234/complete",
    "/api/v1/heartbeat-detail",          # 前缀相似但不是同一端点
]


def _access_record(path: str, status: int) -> logging.LogRecord:
    """uvicorn access 记录的**真实形状**：args=(client, method, full_path, ver, status)。"""
    return logging.LogRecord(
        ACCESS_LOGGER_NAME, logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:45678", "POST", path, "1.1", status),
        None,
    )


@pytest.fixture
def access_logger_restored():
    """Snapshot/restore the ``uvicorn.access`` logger（filter 会在测试间泄漏）。"""
    logger = logging.getLogger(ACCESS_LOGGER_NAME)
    saved_filters = list(logger.filters)
    saved_handlers = list(logger.handlers)
    saved_level = logger.level
    yield logger
    logger.filters[:] = saved_filters
    logger.handlers[:] = saved_handlers
    logger.setLevel(saved_level)


@pytest.mark.parametrize("path", NOISE_PATHS)
def test_noise_filter_drops_successful_polling_lines(path, monkeypatch):
    monkeypatch.delenv(ACCESS_LOG_FULL_ENV, raising=False)
    flt = AccessLogNoiseFilter()
    assert flt.filter(_access_record(path, 200)) is False, f"{path} 的 200 行应被丢弃"
    assert flt.filter(_access_record(f"{path}?verbose=1", 200)) is False, "带 query 同判"


@pytest.mark.parametrize("path", NOISE_PATHS)
@pytest.mark.parametrize("status", [301, 302, 304, 404, 422, 500, 503])
def test_noise_filter_keeps_non_2xx(path, status, monkeypatch):
    """排查面不受影响：这些端点的非 2xx 一条不丢（含 3xx 重定向，#3100）。"""
    monkeypatch.delenv(ACCESS_LOG_FULL_ENV, raising=False)
    assert AccessLogNoiseFilter().filter(_access_record(path, status)) is True


@pytest.mark.parametrize("path", KEPT_PATHS)
def test_noise_filter_keeps_successful_non_polling_lines(path, monkeypatch):
    monkeypatch.delenv(ACCESS_LOG_FULL_ENV, raising=False)
    assert AccessLogNoiseFilter().filter(_access_record(path, 200)) is True


def test_noise_filter_is_not_applied_to_other_loggers():
    """只有 access 行受降噪影响——应用日志的 INFO 不能被吞。"""
    monkeypatch = None  # noqa: F841  （保持签名一致，本用例不需要 env）
    record = logging.LogRecord(
        APP_LOGGER_NAME, logging.INFO, __file__, 1,
        "reconcile_done %s", ("/api/v1/heartbeat",), None,
    )
    assert AccessLogNoiseFilter().filter(record) is True, "args 形状不符时放行（fail-open）"


@pytest.mark.parametrize(
    "args",
    [
        None,
        ("127.0.0.1:1", "POST"),                              # 元组太短
        ("127.0.0.1:1", "POST", "/api/v1/heartbeat", "1.1", "n/a"),  # status 非数字
        ("127.0.0.1:1", "POST", "/api/v1/agent/jobs/claim"),   # 4 元组（缺 status）
    ],
)
def test_noise_filter_fails_open_on_unexpected_record_shape(args, monkeypatch):
    """形状不符时宁可多记一行，不可静默丢证据（判据失配必须表现为「不降噪」）。"""
    monkeypatch.delenv(ACCESS_LOG_FULL_ENV, raising=False)
    record = logging.LogRecord(
        ACCESS_LOGGER_NAME, logging.INFO, __file__, 1, "%s - %s", args, None,
    )
    assert AccessLogNoiseFilter().filter(record) is True


def test_noise_filter_fails_open_when_uvicorn_changes_record_shape(monkeypatch):
    """uvicorn 若把 args 改成 dict 形态，判据认不得 ⇒ **放行**，而不是把内部轮询
    请求**全部**丢掉（判据失配的方向必须是「不降噪」）。"""
    monkeypatch.delenv(ACCESS_LOG_FULL_ENV, raising=False)
    record = logging.LogRecord(
        ACCESS_LOGGER_NAME, logging.INFO, __file__, 1, "%(request_line)s", ("x",), None,
    )
    record.args = {"path": "/api/v1/heartbeat", "status_code": 200}
    assert AccessLogNoiseFilter().filter(record) is True


def test_kill_switch_restores_every_line(monkeypatch):
    monkeypatch.setenv(ACCESS_LOG_FULL_ENV, "1")
    flt = AccessLogNoiseFilter()
    for path in NOISE_PATHS:
        assert flt.filter(_access_record(path, 200)) is True, "STP_ACCESS_LOG_FULL=1 时一条不丢"


def test_is_access_log_noise_truth_table():
    """纯函数判据本身：路径相似但不同端点不得误伤。"""
    assert is_access_log_noise("/api/v1/agent/jobs/9/patrol-heartbeat", 200) is True
    assert is_access_log_noise("/api/v1/agent/jobs/9/patrol-heartbeat", 500) is False
    assert is_access_log_noise("/api/v1/agent/jobs/9/patrol-heartbeat", 302) is False
    assert is_access_log_noise("/api/v1/heartbeat-detail", 200) is False
    assert is_access_log_noise("/api/v1/heartbeat", 200) is True


def test_install_attaches_filter_to_access_logger_once(access_logger_restored):
    logger = access_logger_restored
    logger.filters[:] = []

    install_access_log_filter()
    install_access_log_filter()

    installed = [f for f in logger.filters if isinstance(f, AccessLogNoiseFilter)]
    assert len(installed) == 1, "重复调用不得重复挂载"


def test_install_filter_survives_uvicorn_dictconfig(access_logger_restored):
    """挂在 **logger** 上是对的：uvicorn 的 `dictConfig` 重建 handler，但该 logger 的
    配置项里没有 `filters`，已挂的 filter 不会被清掉（实测钉子——若哪天 uvicorn 加了
    `filters: []`，这条会红，提示改用 handler 级挂载）。"""
    from uvicorn.config import LOGGING_CONFIG

    logging.config.dictConfig(LOGGING_CONFIG)
    access_logger_restored.filters[:] = []
    install_access_log_filter()
    logging.config.dictConfig(LOGGING_CONFIG)

    assert any(isinstance(f, AccessLogNoiseFilter) for f in access_logger_restored.filters)


def test_main_wires_the_access_log_filter():
    """接线钉子：降噪必须真的装在控制面入口上（模块级调用）。"""
    source = (pathlib.Path(__file__).resolve().parents[2] / "main.py").read_text(
        encoding="utf-8"
    )
    assert "install_access_log_filter()" in source, (
        "backend/main.py 未调用 install_access_log_filter()——降噪就只存在于测试里"
    )


def test_access_logger_drops_noise_end_to_end(access_logger_restored, monkeypatch):
    """走**真实 logger** 一遍（不只直接调 filter）：证明 logger 级 filter 确实生效。

    用挂在该 logger 上的捕获 handler，而不是 `caplog`——uvicorn 的配置把
    `uvicorn.access.propagate` 设为 False，root 侧的 caplog 看不到它。
    """
    monkeypatch.delenv(ACCESS_LOG_FULL_ENV, raising=False)
    logger = access_logger_restored
    logger.filters[:] = []
    install_access_log_filter()

    captured: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = captured.append  # type: ignore[method-assign]
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    logger.info('%s - "%s %s HTTP/%s" %d', "1.2.3.4:5", "POST", "/api/v1/heartbeat", "1.1", 200)
    logger.info('%s - "%s %s HTTP/%s" %d', "1.2.3.4:5", "POST", "/api/v1/heartbeat", "1.1", 500)
    logger.info('%s - "%s %s HTTP/%s" %d', "1.2.3.4:5", "GET", "/api/v1/plan-runs/7", "1.1", 200)

    emitted = [r.getMessage() for r in captured]
    assert len(emitted) == 2, emitted
    assert any("500" in m for m in emitted), "非 2xx 必须留下"
    assert any("/api/v1/plan-runs/7" in m for m in emitted), "非轮询端点必须留下"
    assert not any("/api/v1/heartbeat" in m and "200" in m for m in emitted)
