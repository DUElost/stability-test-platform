"""#1958 — 数据库死锁（SQLSTATE 40P01）的可观测性回归。

背景：Job/Lease 锁序死锁在 2026-09 复发约四周（PG 服务日志 83 次/约 20 小时），
而平台侧**零指标零告警**——它只在 PostgreSQL 服务端日志里可见，受害事务还可能被
上层的通用 `except Exception` 吞掉（`_reconcile_expired_leases` 的逐候选失败分支
即如此，还被记成与真实原因不符的 `reconciler_job_load_failed`），业务返回码完全
看不出来。

本文件钉住四件事：
1. 监听器确实注册在两个引擎上（没接上则计数永不发生，属"指标写了但没用"）；
2. 判定只认死锁：40P01 计数，其它 SQLSTATE 不计数，无 sqlstate 时按消息兜底；
3. 观测自身出错不改变异常传播（回调异常只记 debug）；
4. 该指标有对应的告警规则（有指标无规则 = 又一个无人盯的信号）。

无需真实数据库：``handle_error`` 回调是纯函数，测试直接驱动它。
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from prometheus_client import REGISTRY
from sqlalchemy import create_engine, event, text

from backend.core import database
from backend.core.database import (
    AsyncSessionLocal,
    _attach_db_error_metrics,
    _is_deadlock,
)

_PG_ONLY = pytest.mark.skipif(
    os.getenv("DATABASE_URL", "").startswith("sqlite"),
    reason="制造真实死锁需要 PostgreSQL",
)

_METRIC = "stability_db_deadlock_total"
_ALERTS = (
    Path(__file__).resolve().parents[3]
    / "deploy" / "prometheus" / "alerts-stability-platform.yml"
)


class _FakePgError(Exception):
    """模拟 asyncpg / psycopg 的异常：在异常对象上带 ``sqlstate``。"""

    def __init__(self, sqlstate: str, message: str = "database error"):
        super().__init__(message)
        self.sqlstate = sqlstate


def _ctx(orig, statement: str = "SELECT 1") -> SimpleNamespace:
    return SimpleNamespace(original_exception=orig, statement=statement)


def _counter(engine_label: str) -> float:
    return REGISTRY.get_sample_value(_METRIC, {"engine": engine_label}) or 0.0


@pytest.fixture()
def handler():
    """在一次性引擎上注册，拿到可直接驱动的回调（证明注册 + 提供驱动入口）。"""
    engine = create_engine("sqlite://")
    fn = _attach_db_error_metrics(engine, "unittest")
    assert event.contains(engine, "handle_error", fn), "handle_error 监听器未注册"
    yield fn
    engine.dispose()


def test_wiring_on_real_engines():
    """生产接线：两个引擎都必须挂上（否则线上计数恒为 0 而测试仍绿）。"""
    assert "sync" in database._db_error_handlers
    if not database._use_missing_async_runtime:
        assert "async" in database._db_error_handlers


def test_deadlock_increments_counter(handler):
    before = _counter("unittest")
    handler(_ctx(_FakePgError("40P01", "deadlock detected")))
    assert _counter("unittest") == before + 1


@pytest.mark.parametrize("sqlstate", ["40001", "23505", "57014", "08006"])
def test_other_sqlstate_not_counted(handler, sqlstate):
    """只有死锁计入：序列化失败/唯一冲突/取消/断连都不是死锁。"""
    before = _counter("unittest")
    handler(_ctx(_FakePgError(sqlstate)))
    assert _counter("unittest") == before


def test_missing_context_never_counted(handler):
    before = _counter("unittest")
    handler(_ctx(None))
    assert _counter("unittest") == before


def test_message_fallback_without_sqlstate(handler):
    """驱动差异兜底：没有 ``sqlstate`` 属性时按消息判定。"""
    before = _counter("unittest")
    handler(_ctx(RuntimeError("could not serialize access due to ...")))
    assert _counter("unittest") == before
    handler(_ctx(RuntimeError("Deadlock detected")))
    assert _counter("unittest") == before + 1


def test_is_deadlock_helper_edges():
    class _NoState:
        def __str__(self):
            return "deadlock detected"

    assert _is_deadlock(_FakePgError("40P01")) is True
    assert _is_deadlock(_FakePgError("40001")) is False
    assert _is_deadlock(_NoState()) is True
    assert _is_deadlock(None) is False
    assert _is_deadlock("deadlock detected") is True


def test_handler_logs_actionable_warning(handler, caplog):
    with caplog.at_level(logging.WARNING, logger="backend.core.database"):
        handler(_ctx(
            _FakePgError("40P01"),
            statement="UPDATE device_leases SET renewed_at = now()",
        ))
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("db_deadlock_detected" in m for m in messages), messages
    assert any("device_leases" in m for m in messages), messages


def test_metrics_failure_does_not_propagate(handler, monkeypatch):
    """观测不得改变错误传播：计数器后端异常必须被吞成 debug。"""
    def _boom(_label):
        raise RuntimeError("metrics backend down")

    monkeypatch.setattr("backend.core.metrics.record_db_deadlock", _boom)
    handler(_ctx(_FakePgError("40P01")))  # 不抛出即通过


def test_alert_rule_exists_for_deadlock_metric():
    """有指标无规则等于又一个「只记日志没人盯」；规则也须指名该指标。"""
    data = yaml.safe_load(_ALERTS.read_text(encoding="utf-8"))
    exprs = [
        (rule["alert"], rule["expr"])
        for group in data.get("groups", [])
        for rule in group.get("rules", [])
        if "alert" in rule
    ]
    matching = [name for name, expr in exprs if _METRIC in expr]
    assert matching, f"没有任何告警规则引用 {_METRIC}"


# ══════════════════════════════════════════════════════════════════════════════
# 端到端：真实死锁必须被记到 async 引擎上
# ══════════════════════════════════════════════════════════════════════════════


@_PG_ONLY
@pytest.mark.asyncio(loop_scope="module")
async def test_real_deadlock_increments_async_counter():
    """两个会话以相反顺序取同一对 advisory 锁 → PG 报 40P01。

    这条用例证明的是**接线真的生效**，而不是回调本身能算数：如果
    ``handle_error`` 在 async 引擎上不触发（只测回调会漏掉这一点），本用例失败
    ——那样回收器那条路仍然没有任何信号。
    用 advisory lock 而非业务表：只需要「两行、相反顺序」，不必构造 FK 种子。
    """
    before = _counter("async")

    async def _lock_seq(first: int, second: int) -> None:
        async with AsyncSessionLocal() as db:
            await db.execute(
                text("SELECT pg_advisory_xact_lock(:k)"), {"k": first},
            )
            await asyncio.sleep(0.5)
            await db.execute(
                text("SELECT pg_advisory_xact_lock(:k)"), {"k": second},
            )
            await db.rollback()

    results = await asyncio.gather(
        _lock_seq(987_601, 987_602),
        _lock_seq(987_602, 987_601),
        return_exceptions=True,
    )

    deadlocks = [
        r for r in results
        if isinstance(r, BaseException) and "deadlock" in str(r).lower()
    ]
    assert deadlocks, f"未制造出真实死锁（用例前提不成立）: {results}"
    assert _counter("async") == before + 1, (
        "真实死锁未被计入 stability_db_deadlock_total{engine=\"async\"} —— "
        "handle_error 接线对 async 引擎未生效"
    )

