"""#2741 — audit_logs 分层保留期裁剪（ADR-0049）。

覆盖：三层 cutoff 独立性（30/90/180）、business 默认桶对未知 action 的
NOT IN 封闭性、单 tick 批次有界且下一 tick 自然推进（无 #1827 形态的
饿死）、汇总审计仅在有删除时写入且自身不豁免（自免环）。

``audit_log_cleanup_job`` 使用模块级 ``SessionLocal`` 与 ``SchedulerSettings``
——测试经 monkeypatch 指向 fixture session，天数经 ``scheduler_env``
覆写（写后清缓存，ADR-0042）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.models.audit import AuditLog
from backend.scheduler import audit_log_cleanup
from backend.scheduler.audit_log_cleanup import SUMMARY_ACTION, audit_log_cleanup_job


@pytest.fixture
def audit_env(db_session, monkeypatch, scheduler_env):
    """裁剪环境：SessionLocal → fixture session；返回造行助手。"""
    monkeypatch.setattr(audit_log_cleanup, "SessionLocal", lambda: db_session)

    def _mk(action: str, age_days: float, *, resource_type: str = "plan") -> AuditLog:
        row = AuditLog(
            action=action,
            resource_type=resource_type,
            details={},
            timestamp=datetime.now(timezone.utc) - timedelta(days=age_days),
        )
        db_session.add(row)
        # job 自带事务与 close——未提交的夹具行会被 close 一并丢弃（同
        # test_retention_cleanup 的坑）。
        db_session.commit()
        return row

    return _mk


def _set_days(scheduler_env) -> None:
    """显式钉住三层天数与批大小（默认值同款；防外部 env 污染）。"""
    for name, value in (
        ("AUDIT_LOG_SESSION_RETENTION_DAYS", "30"),
        ("AUDIT_LOG_BUSINESS_RETENTION_DAYS", "90"),
        ("AUDIT_LOG_SECURITY_RETENTION_DAYS", "180"),
        ("AUDIT_LOG_RETENTION_BATCH_SIZE", "5000"),
    ):
        scheduler_env(name, value)


def test_layer_cutoffs_independent(audit_env, db_session, scheduler_env):
    """session 30d / business 90d / security 180d 各自独立判到期。"""
    _set_days(scheduler_env)
    audit_env("refresh", 31)        # session：过期 → 删
    audit_env("refresh", 29)        # session：未到 → 留
    audit_env("job_terminalized", 91)  # business：过期 → 删
    audit_env("plan_created", 89)      # business：未到 → 留
    audit_env("login_failed", 170)     # security：未到 → 留
    audit_env("login_locked", 181)     # security：过期 → 删

    pruned = audit_log_cleanup_job()

    assert pruned == {"session": 1, "business": 1, "security": 1}
    remaining = {row.action for row in db_session.query(AuditLog).all()}
    # SUMMARY_ACTION = 有删除的 tick 写入的汇总审计（business 层）
    assert remaining == {"refresh", "plan_created", "login_failed", SUMMARY_ACTION}


def test_business_default_bucket_covers_unknown_actions(
    audit_env, db_session, scheduler_env
):
    """未登记的新 action 自动落 business 90d——NOT IN 封闭，不需要登记动作。"""
    _set_days(scheduler_env)
    audit_env("brand_new_action_never_registered", 91)
    audit_env("another_unknown_one", 89)

    pruned = audit_log_cleanup_job()

    assert pruned == {"session": 0, "business": 1, "security": 0}
    actions = {row.action for row in db_session.query(AuditLog).all()}
    assert "another_unknown_one" in actions
    assert "brand_new_action_never_registered" not in actions


def test_security_outlives_business_cutoff(audit_env, db_session, scheduler_env):
    """90d < age < 180d 的安全事件仍保留；且无删除的 tick 不写汇总审计。"""
    _set_days(scheduler_env)
    audit_env("change_password_failed", 120)

    pruned = audit_log_cleanup_job()

    assert pruned == {"session": 0, "business": 0, "security": 0}
    # idle tick 零噪声：不产生 audit_retention_pruned 行
    assert db_session.query(AuditLog).filter_by(action=SUMMARY_ACTION).count() == 0
    assert db_session.query(AuditLog).filter_by(
        action="change_password_failed"
    ).count() == 1


def test_batch_size_bounds_one_tick(audit_env, db_session, scheduler_env):
    """单 tick 每层至多删 batch 行；下一 tick 从剩余里继续（自然推进）。"""
    scheduler_env("AUDIT_LOG_SESSION_RETENTION_DAYS", "0")  # 该层全量到期
    scheduler_env("AUDIT_LOG_BUSINESS_RETENTION_DAYS", "3650")
    scheduler_env("AUDIT_LOG_SECURITY_RETENTION_DAYS", "3650")
    scheduler_env("AUDIT_LOG_RETENTION_BATCH_SIZE", "2")
    for _ in range(5):
        audit_env("refresh", 0.01)

    first = audit_log_cleanup_job()
    second = audit_log_cleanup_job()

    assert first["session"] == 2
    assert second["session"] == 2
    assert db_session.query(AuditLog).filter_by(action="refresh").count() == 1


def test_summary_row_is_self_pruning(audit_env, db_session, scheduler_env):
    """汇总审计每 tick 至多一条，且自身不豁免于裁剪谓词（自免环）。"""
    scheduler_env("AUDIT_LOG_SESSION_RETENTION_DAYS", "3650")
    scheduler_env("AUDIT_LOG_SECURITY_RETENTION_DAYS", "3650")
    scheduler_env("AUDIT_LOG_BUSINESS_RETENTION_DAYS", "1")
    audit_env("job_terminalized", 2)

    first = audit_log_cleanup_job()
    assert first["business"] == 1

    summaries = db_session.query(AuditLog).filter_by(action=SUMMARY_ACTION).all()
    assert len(summaries) == 1
    assert summaries[0].details["pruned"] == {
        "session": 0, "business": 1, "security": 0,
    }

    # 汇总行回拨到 business 保留期之外 → 下一 tick 被裁掉（不豁免），
    # 同时本 tick 又写入新的汇总——数量守恒为 1，不随 tick 累积。
    summaries[0].timestamp = datetime.now(timezone.utc) - timedelta(days=2)
    db_session.commit()

    second = audit_log_cleanup_job()
    assert second["business"] == 1  # 删掉的正是上一条汇总
    assert db_session.query(AuditLog).filter_by(action=SUMMARY_ACTION).count() == 1


# ── #2789 顺带项：已提交的删除是既成事实，任何后续失败都不能把读数改成「没删」──


def _pruned_sample(audit_env, scheduler_env) -> None:
    """造一批「必然有删除」的夹具行，并钉住天数（避免 idle tick 走不到汇总分支）。"""
    _set_days(scheduler_env)
    scheduler_env("AUDIT_LOG_SESSION_RETENTION_DAYS", "0")     # session 层全量到期
    audit_env("refresh", 1)
    audit_env("refresh", 1)


def test_summary_audit_failure_still_reports_committed_deletions(
    audit_env, db_session, scheduler_env, monkeypatch, caplog
) -> None:
    """汇总审计写入失败 → 返回值/指标/日志三者必须仍然说「删了 N 行」。

    旧实现在这里落到外层 except：行已删、`audit_retention_pruned_total` 已自增，
    但函数返回全 0 并记 `audit_log_cleanup_failed` —— 对账时三方各说一套话（#2789）。
    ADR-0049 D5 的本意是「这条审计失败只丢这一条审计」，不含把删除事实抹平。
    """
    from prometheus_client import REGISTRY

    _pruned_sample(audit_env, scheduler_env)

    def _boom(*_a, **_k):
        raise RuntimeError("simulated audit write failure")

    monkeypatch.setattr(audit_log_cleanup, "record_audit", _boom)

    before = REGISTRY.get_sample_value(
        "stability_audit_retention_pruned_total", {"layer": "session"}
    ) or 0.0
    with caplog.at_level("INFO"):
        pruned = audit_log_cleanup_job()

    # 1) 返回值说真话
    assert pruned["session"] == 2
    # 2) 数据库里确实删了（返回值不是自我宣称）
    assert db_session.query(AuditLog).filter_by(action="refresh").count() == 0
    # 3) 指标与返回值同向（三方自洽）
    after = REGISTRY.get_sample_value(
        "stability_audit_retention_pruned_total", {"layer": "session"}
    ) or 0.0
    assert after - before == 2.0
    # 4) 失败有**自己的**名字，且不冒充「裁剪整体失败」
    text = caplog.text
    assert "audit_retention_summary_audit_failed" in text
    assert "audit_log_cleanup_failed" not in text
    assert "summary_audit_failed=1" in text


def test_summary_audit_row_absent_when_its_write_fails(
    audit_env, db_session, scheduler_env, monkeypatch
) -> None:
    """失败的那一笔审计不落库，且**不污染**已提交的删除（回滚只作用于这一笔）。"""
    _pruned_sample(audit_env, scheduler_env)

    def _boom(*_a, **_k):
        raise RuntimeError("simulated audit write failure")

    monkeypatch.setattr(audit_log_cleanup, "record_audit", _boom)
    audit_log_cleanup_job()

    assert db_session.query(AuditLog).filter_by(action=SUMMARY_ACTION).count() == 0
    assert db_session.query(AuditLog).filter_by(action="refresh").count() == 0


def test_prune_transaction_failure_still_reports_zero(
    audit_env, db_session, scheduler_env, monkeypatch, caplog
) -> None:
    """真·裁剪失败（事务未提交）才许报零——两条失败路径必须可区分。

    没有这条，前一条用例的「就地接住异常」会被误推广成「任何失败都报已删」，
    那是反向的读数失真。
    """
    _pruned_sample(audit_env, scheduler_env)
    original = audit_log_cleanup._prune_layer
    calls = {"n": 0}

    def _flaky(session, *, actions, cutoff, limit):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated prune failure inside the transaction")
        return original(session, actions=actions, cutoff=cutoff, limit=limit)

    monkeypatch.setattr(audit_log_cleanup, "_prune_layer", _flaky)

    with caplog.at_level("INFO"):
        pruned = audit_log_cleanup_job()

    assert pruned == {"session": 0, "business": 0, "security": 0}
    assert "audit_log_cleanup_failed" in caplog.text
    assert "audit_retention_summary_audit_failed" not in caplog.text
    # 事务回滚：夹具行还在（报零与事实一致）
    assert db_session.query(AuditLog).filter_by(action="refresh").count() == 2
