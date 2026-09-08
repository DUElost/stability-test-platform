"""#936 — run_retention_cleanup 链式引用安全。

parent/root 自引用 FK 无删除级联：父到期而子未到期/仍运行时，按批次直删
违反 FK、整批回滚（且每轮必重选同批 → 僵尸积压）。修复语义 = 删除前计算
引用闭包保留集（外部未删 Run 引用的批内 id 沿祖先链传播），只删安全叶子。

cron_scheduler.run_retention_cleanup 使用模块级 ``SessionLocal`` 与
``PLAN_RUN_RETENTION_DAYS``——测试经 monkeypatch 指向 fixture session 与 0 天
保留（started_at < now 即入选）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.scheduler import cron_scheduler


@pytest.fixture
def cleanup_env(db_session, monkeypatch):
    monkeypatch.setattr(cron_scheduler, "PLAN_RUN_RETENTION_DAYS", 0)
    monkeypatch.setattr(cron_scheduler, "SessionLocal", lambda: db_session)
    plan = Plan(name="retention-chain")
    db_session.add(plan)
    db_session.flush()
    return db_session, plan


def _mk_run(db, plan, *, status="SUCCESS", age_days=10, parent=None, root=None):
    run = PlanRun(
        plan_id=plan.id,
        status=status,
        failure_threshold=0.05,
        plan_snapshot={},
        run_type="MANUAL",
        started_at=datetime.now(timezone.utc) - timedelta(days=age_days),
        parent_plan_run_id=parent.id if parent else None,
        root_plan_run_id=root.id if root else None,
    )
    db.add(run)
    # cleanup 退出 with SessionLocal 时会 close（回滚未提交事务）——fixture
    # 数据必须先落库，否则被 cleanup 的 close 一并丢弃。
    db.commit()
    return run


def test_parent_kept_when_child_still_running(cleanup_env):
    """#936 验收主场景：父到期、子仍运行 → 父保留、不整批回滚。"""
    db, plan = cleanup_env
    parent = _mk_run(db, plan, age_days=10)
    _mk_run(db, plan, status="RUNNING", age_days=0, parent=parent)  # 子运行中

    cron_scheduler.run_retention_cleanup()

    assert db.query(PlanRun).filter_by(id=parent.id).one() is not None


def test_chain_all_expired_deleted(cleanup_env):
    """全链到期：祖→父→子全部删除（无引用阻碍）。"""
    db, plan = cleanup_env
    root = _mk_run(db, plan, age_days=10)
    mid = _mk_run(db, plan, age_days=9, parent=root, root=root)
    leaf = _mk_run(db, plan, age_days=8, parent=mid, root=root)

    cron_scheduler.run_retention_cleanup()

    assert db.query(PlanRun).count() == 0
    assert {root.id, mid.id, leaf.id} and not db.query(PlanRun).filter(
        PlanRun.id.in_([root.id, mid.id, leaf.id])
    ).first()


def test_grandparent_kept_via_ancestry_propagation(cleanup_env):
    """孙运行中：父与祖父经 parent/root 传播全部保留。"""
    db, plan = cleanup_env
    root = _mk_run(db, plan, age_days=10)
    mid = _mk_run(db, plan, age_days=9, parent=root, root=root)
    leaf = _mk_run(db, plan, status="RUNNING", age_days=0, parent=mid, root=root)  # 孙运行中

    cron_scheduler.run_retention_cleanup()

    remaining = {r.id for r in db.query(PlanRun).all()}
    assert remaining == {root.id, mid.id, leaf.id}  # 祖先链保留 + 运行中孙本身


def test_root_referenced_by_active_run_kept(cleanup_env):
    """链根被运行中 Run 的 root 引用 → 保留。"""
    db, plan = cleanup_env
    root = _mk_run(db, plan, age_days=10)
    _mk_run(db, plan, status="RUNNING", age_days=0, root=root)  # 仅 root 引用

    cron_scheduler.run_retention_cleanup()

    assert db.query(PlanRun).filter_by(id=root.id).one() is not None


def test_unreferenced_runs_deleted_normally(cleanup_env):
    """无链引用 → 正常删除（既有行为回归）。"""
    db, plan = cleanup_env
    a = _mk_run(db, plan, age_days=10)
    b = _mk_run(db, plan, age_days=10)

    cron_scheduler.run_retention_cleanup()

    assert db.query(PlanRun).count() == 0
    assert a.id != b.id
