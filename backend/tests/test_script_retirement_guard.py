"""#735 退役判据与巡检守卫（`backend/services/script_retirement.py`）。

判据此前只活在人工批量执行的临时 SQL 与一份 note 里；本文件把它钉成契约：
CI 侧锁**判据函数与 CLI 退出码**（不依赖生产数据——CI 不得连生产库），
生产侧「超期零引用仍活跃」的实际告警由 `--guard` 模式承担（运维/巡检调用）。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.models.script import Script
from backend.services.script_retirement import (
    KEEP_INACTIVE,
    KEEP_LATEST_ACTIVE,
    KEEP_RECENT_USE,
    KEEP_REFERENCED,
    RETIRE,
    STALE_COOLDOWN_DAYS,
    InconsistentRetirementPlan,
    ScriptVersionFact,
    assert_no_family_emptied,
    classify,
    days_until_cooldown_expiry,
    latest_active_versions,
    retirement_candidates,
    version_key,
)

TODAY = date(2026, 9, 16)


def _fact(name="gpu_setup", version="1.0.0", *, active=True, refs=0, last_used=None):
    return ScriptVersionFact(
        name=name, version=version, is_active=active, refs=refs, last_used_on=last_used
    )


def _decide(facts, *, cooldown_days=STALE_COOLDOWN_DAYS):
    verdicts = classify(facts, today=TODAY, cooldown_days=cooldown_days)
    return {key: v.decision for key, v in verdicts.items()}


# --------------------------------------------------------------------------
# 判据本体
# --------------------------------------------------------------------------

def test_version_key_compares_numerically():
    assert version_key("1.0.10") > version_key("1.0.2")
    assert version_key("v1.3.16") > version_key("1.3.15")
    assert version_key("1.0") < version_key("1.0.0")
    assert version_key("1.0.0-rc1") > version_key("1.0.0")  # 非数字段退化比较，不抛


def test_referenced_and_inactive_are_kept():
    facts = [
        _fact(version="1.0.0", refs=2),
        _fact(version="1.0.1", active=False),
        _fact(version="1.0.2"),
    ]
    decided = _decide(facts)
    assert decided[("gpu_setup", "1.0.0")] == KEEP_REFERENCED
    assert decided[("gpu_setup", "1.0.1")] == KEEP_INACTIVE
    assert decided[("gpu_setup", "1.0.2")] == KEEP_LATEST_ACTIVE


def test_referenced_takes_precedence_over_latest_active():
    # 最新版同时被引用 → 仍是 KEEP_REFERENCED（守卫语义优先，供 409 对账）
    assert _decide([_fact(version="2.0.0", refs=1)])[("gpu_setup", "2.0.0")] == KEEP_REFERENCED


def test_superseded_version_without_usage_is_retirable():
    facts = [_fact(version="1.0.0"), _fact(version="1.0.1"), _fact(version="1.2.0")]
    decided = _decide(facts)
    assert decided[("gpu_setup", "1.0.0")] == RETIRE
    assert decided[("gpu_setup", "1.0.1")] == RETIRE
    assert decided[("gpu_setup", "1.2.0")] == KEEP_LATEST_ACTIVE
    assert [f.version for f in retirement_candidates(facts, today=TODAY)] == ["1.0.0", "1.0.1"]


def test_cooldown_boundary_is_inclusive():
    facts = [
        _fact(version="1.0.0", last_used=date(2026, 7, 19)),  # 59 天前
        _fact(version="1.0.1", last_used=date(2026, 7, 18)),  # 60 天前
        _fact(version="2.0.0"),
    ]
    decided = _decide(facts)
    assert decided[("gpu_setup", "1.0.0")] == KEEP_RECENT_USE
    assert decided[("gpu_setup", "1.0.1")] == RETIRE


def test_exemption_follows_latest_active_not_latest_row():
    """最新版已停用（草稿目录被删 / #2048 回归版）时，豁免必须落到次新 active。"""
    facts = [
        _fact(version="1.0.0"),
        _fact(version="1.0.1"),
        _fact(version="2.0.0", active=False),
    ]
    assert latest_active_versions(facts) == {"gpu_setup": "1.0.1"}
    decided = _decide(facts)
    assert decided[("gpu_setup", "1.0.1")] == KEEP_LATEST_ACTIVE
    assert decided[("gpu_setup", "1.0.0")] == RETIRE


def test_latest_active_exemption_exits_when_newer_active_lands():
    """B-6 退出判据：同族出现更新的 active 版本，旧最新版即失去豁免。"""
    before = [_fact(version="1.0.0", refs=1), _fact(version="1.1.0")]
    assert _decide(before)[("gpu_setup", "1.1.0")] == KEEP_LATEST_ACTIVE

    after = before + [_fact(version="1.2.0")]
    assert _decide(after)[("gpu_setup", "1.1.0")] == RETIRE
    assert _decide(after)[("gpu_setup", "1.2.0")] == KEEP_LATEST_ACTIVE


def test_cooldown_expiry_date_is_last_use_plus_window():
    fact = _fact(version="1.0.0", last_used=date(2026, 8, 5))
    assert days_until_cooldown_expiry(fact, today=TODAY) == date(2026, 10, 4)
    assert days_until_cooldown_expiry(_fact(version="1.0.0"), today=TODAY) is None
    assert days_until_cooldown_expiry(_fact(version="1.0.0", refs=3), today=TODAY) is None


def test_family_can_never_be_emptied_raises():
    """fail-loud 闸：手工构造「把整族都判成可退役」的结论集，必须抛。"""
    facts = [_fact(version="1.0.0"), _fact(version="1.0.1")]
    broken = {
        ("gpu_setup", "1.0.0"): classify(facts, today=TODAY)[("gpu_setup", "1.0.0")],
        ("gpu_setup", "1.0.1"): classify(facts, today=TODAY)[("gpu_setup", "1.0.1")],
    }
    from backend.services.script_retirement import Verdict

    broken[("gpu_setup", "1.0.1")] = Verdict(RETIRE, "人为破坏豁免")
    with pytest.raises(InconsistentRetirementPlan, match="失去全部 active 版本"):
        assert_no_family_emptied(facts, broken)


# --------------------------------------------------------------------------
# 文档↔代码互锁（判据常量与通道口径不得只改一侧）
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "needle",
    [
        f"{STALE_COOLDOWN_DAYS} 天",          # 冷却期数值
        "DELETE /api/v1/scripts/{id}",        # 专用软退役通道
        "script_retirement.py",               # 判据唯一事实源指针
    ],
)
def test_judgment_contract_is_documented(needle):
    # 不依赖 pytest 的启动目录（CI 与本地 cwd 不同）
    doc = (Path(__file__).resolve().parents[2]
           / "docs" / "development" / "script-versioning.md").read_text(encoding="utf-8")
    assert needle in doc, f"权威文档缺 {needle!r}——改了判据就要同步文档"


# --------------------------------------------------------------------------
# SQL 侧：引用计数与执行事实（真 PG，经 db_session）
# --------------------------------------------------------------------------

def _seed(db_session):
    db_session.add_all([
        Script(name="flash_firmware", script_type="python", version="1.3.5",
               nfs_path="/s/a", content_sha256="a", is_active=True),
        Script(name="flash_firmware", script_type="python", version="1.3.6",
               nfs_path="/s/b", content_sha256="b", is_active=True),
        Script(name="flash_firmware", script_type="python", version="1.3.7",
               nfs_path="/s/c", content_sha256="c", is_active=True),
    ])
    plan = Plan(name="flash-chain")
    db_session.add(plan)
    db_session.flush()
    db_session.add(PlanStep(plan_id=plan.id, step_key="init", script_name="flash_firmware",
                            script_version="1.3.7", stage="init", retry=0))
    db_session.add(PlanRun(
        plan_id=plan.id, run_type="MANUAL", status="SUCCESS",
        started_at="2026-08-05T00:00:00+00:00",
        plan_snapshot={"steps": [
            {"script_name": "flash_firmware", "script_version": "1.3.5"},
            {"script_name": "flash_firmware", "script_version": "1.3.5"},
        ]},
    ))
    db_session.commit()


def test_usage_facts_and_reference_counts_agree_on_pg(db_session):
    from backend.scripts.check_unreferenced_script_versions import (
        build_facts,
        compute_reference_counts,
        compute_usage_facts,
    )

    _seed(db_session)
    rows = compute_reference_counts(db_session)
    usage = compute_usage_facts(db_session)
    assert usage[("flash_firmware", "1.3.5")] == date(2026, 8, 5)

    facts = build_facts(rows, usage)
    decided = classify(facts, today=TODAY)
    assert decided[("flash_firmware", "1.3.5")].decision == KEEP_RECENT_USE   # 42 天前跑过
    assert decided[("flash_firmware", "1.3.6")].decision == RETIRE             # 零引用零执行
    assert decided[("flash_firmware", "1.3.7")].decision == KEEP_REFERENCED    # 被 Plan 钉住


def test_usage_facts_unavailable_is_fail_loud(monkeypatch, db_session):
    """非 PG 方言（无 jsonb）不得被当成「从未执行」——否则巡检偏向过度退役。"""
    from backend.scripts import check_unreferenced_script_versions as mod

    class _Boom:
        def execute(self, *a, **kw):
            raise RuntimeError("jsonb_array_elements does not exist")

    with pytest.raises(mod.UsageFactsUnavailable):
        mod.compute_usage_facts(_Boom())


# --------------------------------------------------------------------------
# CLI 退出码契约（巡检模式是门禁，默认模式不是）
# --------------------------------------------------------------------------

def _patch_db(monkeypatch, mod, *, rows, usage=None, usage_error=None):
    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _Engine:
        def connect(self):
            return _Conn()

        def dispose(self):
            pass

    monkeypatch.setattr(mod, "create_engine", lambda url: _Engine())
    monkeypatch.setattr(mod, "resolve_database_url", lambda: ("postgresql+psycopg://x", "test"))
    monkeypatch.setattr(mod, "compute_reference_counts", lambda conn: rows)
    if usage_error is not None:
        def _boom(conn):
            raise mod.UsageFactsUnavailable(usage_error)
        monkeypatch.setattr(mod, "compute_usage_facts", _boom)
    else:
        monkeypatch.setattr(mod, "compute_usage_facts", lambda conn: usage or {})


@pytest.mark.parametrize(
    "guard,expected",
    [(False, 0), (True, 1)],  # 默认恒 0（诊断），--guard 有到期项即 1
)
def test_cli_guard_exit_code(monkeypatch, capsys, guard, expected):
    from backend.scripts import check_unreferenced_script_versions as mod

    rows = [
        {"name": "s", "version": "1.0.0", "is_active": True, "refs": 0},
        {"name": "s", "version": "1.1.0", "is_active": True, "refs": 0},
    ]
    _patch_db(monkeypatch, mod, rows=rows)
    argv = ["--guard", "--today", TODAY.isoformat()] if guard else ["--today", TODAY.isoformat()]
    assert mod.main(argv) == expected


def test_cli_guard_unknown_when_usage_facts_missing(monkeypatch, capsys):
    from backend.scripts import check_unreferenced_script_versions as mod

    rows = [{"name": "s", "version": "1.0.0", "is_active": True, "refs": 0}]
    _patch_db(monkeypatch, mod, rows=rows, usage_error="no jsonb")
    assert mod.main(["--guard", "--today", TODAY.isoformat()]) == 2


def test_cli_json_carries_plan_and_guard_status_without_trailing_noise(monkeypatch, capsys):
    import json

    from backend.scripts import check_unreferenced_script_versions as mod

    rows = [
        {"name": "s", "version": "1.0.0", "is_active": True, "refs": 0},
        {"name": "s", "version": "1.1.0", "is_active": True, "refs": 0},
    ]
    _patch_db(monkeypatch, mod, rows=rows)
    assert mod.main(["--json", "--guard", "--today", TODAY.isoformat()]) == 1
    payload = json.loads(capsys.readouterr().out)  # --json 输出必须可被 jq 直接吃
    assert payload["guard"] == {"status": "FAIL", "violations": 1}
    assert [i["name"] + "@" + i["version"] for i in payload["retirement_plan"]] == ["s@1.0.0"]
    assert payload["hold"]["latest_active"][0]["version"] == "1.1.0"
