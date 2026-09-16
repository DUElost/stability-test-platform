"""#2285：`run_context` 分段写必须走库端 `jsonb_set`——整段读改写回会抹掉并发写者的键。

时序（下面用例逐字实现）：会话 A 先加载 PlanRun（拿到快照）→ 会话 B 写完自己的
section 并提交 → A 写另一个 section。旧实现基于 A 的旧快照整段回写，B 的键消失。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.models.plan_run import PlanRun
from backend.services.plan_run_context import write_run_context_section


def test_concurrent_section_writes_do_not_drop_each_other(db_session, sample_plan_run, engine):
    run_id = sample_plan_run.id
    with Session(engine) as session_a, Session(engine) as session_b:
        # A 先把行读进自己的 identity map（快照里还没有 B 的键）。
        stale = session_a.get(PlanRun, run_id)
        assert stale is not None
        assert "concurrent" not in (stale.run_context or {})

        assert write_run_context_section(
            session_b, run_id, "concurrent", {"from": "B"},
        ) is True

        # A 接着写另一个 section：不得把 B 刚落下的键抹掉。
        assert write_run_context_section(
            session_a, run_id, "second", {"from": "A"},
        ) is True

    db_session.expire_all()
    rc = db_session.get(PlanRun, run_id).run_context
    assert rc["concurrent"] == {"from": "B"}, "并发写者的键被整段回写抹掉了（#2285）"
    assert rc["second"] == {"from": "A"}


def test_missing_plan_run_returns_false(db_session):
    """不存在的行为 False（原语义保持）。"""
    assert write_run_context_section(db_session, 10 ** 9, "any", {"a": 1}) is False


def test_null_run_context_is_rebuilt_as_object(db_session, sample_plan_run):
    """run_context 为 JSON null（SQLAlchemy 的 None 落库形态）时按空对象重建。"""
    from sqlalchemy import text

    db_session.execute(
        text("UPDATE plan_run SET run_context = 'null'::jsonb WHERE id = :run_id"),
        {"run_id": sample_plan_run.id},
    )
    db_session.commit()

    assert write_run_context_section(
        db_session, sample_plan_run.id, "first", {"a": 1},
    ) is True
    db_session.expire_all()
    assert db_session.get(PlanRun, sample_plan_run.id).run_context == {"first": {"a": 1}}
