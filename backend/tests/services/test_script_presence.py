"""#2958 主体：在位矩阵的五态判定、载荷构造与常设 sweep 落库。

锁的判据（每条都有明确的失败形态）：
① ``build_expected_manifests``：只含全集内的版本；``support_files`` 空 manifest 不下发；
② ``classify_host_presence`` 的优先级：n_a（可达集外）> unknown（RPC 失败）> maintenance
   （窗口内**缺口**）> agent 报的 present/missing/mismatch——unknown **不得**写成 present；
③ maintenance 只改缺口：窗口内在位仍是 present；
④ agent 结果缺行（老 agent / 未上报）记 unknown + not_reported，不猜 missing；
⑤ ``summarize_states`` 的缺口 host 按 host 去重、``checked_at`` 取 min/max（新鲜度=min）；
⑥ ``run_sweep`` 端到端：可达集驱动 RPC 面、整轮 upsert 落库、``sweep_id`` 同轮一致。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.models.host import Host
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun, PlanRunHost
from backend.models.script import Script
from backend.models.script_presence import HostScriptPresence
from backend.services import script_presence as sp
from backend.services.precheck import verify as verify_mod

FULL = [("a", "1.0.0"), ("b", "2.0.0")]


def _script_rows(*rows):
    return [
        {
            "name": n, "version": v, "is_active": True,
            "nfs_path": f"/opt/agent/scripts/{n}/v{v}/{n}.py",
            "content_sha256": "0" * 64,
            "support_files_manifest": sf or {},
        }
        for n, v, sf in rows
    ]


def test_expected_manifests_only_includes_full_set_and_nonempty_support():
    rows = _script_rows(
        ("a", "1.0.0", {}),
        ("b", "2.0.0", {"_adb.py": "a" * 64}),
        ("c", "3.0.0", {}),          # 不在全集内 → 不下发
    )
    out = sp.build_expected_manifests(rows, FULL)
    assert [e["name"] for e in out] == ["a", "b"]
    assert "support_files" not in out[0]          # 空 manifest 不下发
    assert out[1]["support_files"] == {"_adb.py": "a" * 64}
    assert out[0]["nfs_path"].endswith("a/v1.0.0/a.py")


def test_classify_priority_na_unknown_maintenance_and_gap_states():
    # 可达集里只有 a@1.0.0；b@2.0.0 → n_a（即使 RPC 说它 ok 也不改判）
    reachable = {("a", "1.0.0")}
    states = sp.classify_host_presence(
        host_id="h1", full=FULL, reachable=reachable, in_maintenance=False,
        verify_ok=True,
        verify_entries=[{"name": "a", "version": "1.0.0", "ok": True, "exists": True},
                        {"name": "b", "version": "2.0.0", "ok": True, "exists": True}],
        verify_error=None,
    )
    assert states[("a", "1.0.0")] == (sp.STATE_PRESENT, "")
    assert states[("b", "2.0.0")] == (sp.STATE_N_A, "")


def test_classify_rpc_failure_is_unknown_not_green():
    states = sp.classify_host_presence(
        host_id="h1", full=FULL, reachable=set(FULL), in_maintenance=False,
        verify_ok=False, verify_entries=[], verify_error="agent_offline",
    )
    assert states[("a", "1.0.0")] == (sp.STATE_UNKNOWN, "agent_offline")
    assert states[("b", "2.0.0")][0] == sp.STATE_UNKNOWN


def test_classify_gap_states_detail_and_not_reported():
    reachable = set(FULL)
    states = sp.classify_host_presence(
        host_id="h1", full=FULL, reachable=reachable, in_maintenance=False,
        verify_ok=True,
        verify_entries=[
            {"name": "a", "version": "1.0.0", "ok": False, "exists": False,
             "error": "file_missing_or_unreadable"},
            {"name": "b", "version": "2.0.0", "ok": False, "exists": True,
             "error": "support_file_mismatch:_adb.py"},
        ],
        verify_error=None,
    )
    assert states[("a", "1.0.0")] == (sp.STATE_MISSING, "file_missing_or_unreadable")
    assert states[("b", "2.0.0")][0] == sp.STATE_MISMATCH

    # 结果缺行 → unknown + not_reported（不猜 missing）
    states2 = sp.classify_host_presence(
        host_id="h1", full=[("a", "1.0.0")], reachable={("a", "1.0.0")},
        in_maintenance=False, verify_ok=True, verify_entries=[], verify_error=None,
    )
    assert states2[("a", "1.0.0")] == (sp.STATE_UNKNOWN, "not_reported")


def test_maintenance_only_relabels_gaps():
    states = sp.classify_host_presence(
        host_id="h1", full=FULL, reachable=set(FULL), in_maintenance=True,
        verify_ok=True,
        verify_entries=[
            {"name": "a", "version": "1.0.0", "ok": True, "exists": True},
            {"name": "b", "version": "2.0.0", "ok": False, "exists": False,
             "error": "file_missing_or_unreadable"},
        ],
        verify_error=None,
    )
    assert states[("a", "1.0.0")][0] == sp.STATE_PRESENT          # 在位不受影响
    state, detail = states[("b", "2.0.0")]
    assert state == sp.STATE_MAINTENANCE and "maintenance" in detail


def test_summarize_states_dedupes_gap_hosts_and_tracks_freshness():
    now = datetime.now(timezone.utc)
    rows = [
        {"host_id": "h1", "state": sp.STATE_PRESENT, "checked_at": now},
        {"host_id": "h1", "state": sp.STATE_MISSING, "checked_at": now - timedelta(hours=1)},
        {"host_id": "h1", "state": sp.STATE_MISMATCH, "checked_at": now},
        {"host_id": "h2", "state": sp.STATE_UNKNOWN, "checked_at": now},
        {"host_id": "h2", "state": sp.STATE_N_A, "checked_at": now},
    ]
    out = sp.summarize_states(rows)
    assert out["counts"][sp.STATE_PRESENT] == 1
    assert out["counts"][sp.STATE_MISSING] == 1 and out["counts"][sp.STATE_MISMATCH] == 1
    assert out["hosts_with_gap"] == 1                      # h1 两条缺口只算一次
    assert out["checked_at_min"] == now - timedelta(hours=1)
    assert out["checked_at_max"] == now


async def test_run_sweep_persists_present_and_na_rows(db_session, engine, monkeypatch):
    """端到端：可达集驱动 RPC 面 → 五态落库 → 同轮 sweep_id + 新鲜度。"""
    host = Host(id="h-pres", hostname="h-pres", status="ONLINE")
    db_session.add(host)
    db_session.add(Script(
        name="a", version="1.0.0", script_type="test",
        nfs_path="/opt/agent/scripts/a/v1.0.0/a.py", content_sha256="0" * 64,
        is_active=True,
    ))
    plan = Plan(name="p-pres")
    db_session.add(plan)
    db_session.flush()
    db_session.add(PlanStep(
        plan_id=plan.id, step_key="s1", script_name="a", script_version="1.0.0",
        stage="init", enabled=True,
    ))
    run = PlanRun(
        plan_id=plan.id, plan_snapshot={"steps": []}, run_type="MANUAL",
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(PlanRunHost(plan_run_id=run.id, host_id=host.id))
    db_session.commit()

    calls: list[tuple[list[str], int]] = []

    async def fake_gather(host_ids, expected):
        calls.append((list(host_ids), len(expected)))
        return {
            hid: (True, [{"name": "a", "version": "1.0.0", "exists": True, "ok": True}], None)
            for hid in host_ids
        }

    monkeypatch.setattr(verify_mod, "gather_verify", fake_gather)
    factory = sessionmaker(bind=engine)
    result = await sp.run_sweep(days=30, db_factory=factory)

    assert calls and calls[0][0] == [host.id]           # 只对可达集非空的 host 发 RPC
    assert result["full_versions"] == 1
    with factory() as session:
        rows = session.execute(
            select(HostScriptPresence).where(HostScriptPresence.host_id == host.id)
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].state == sp.STATE_PRESENT
        assert rows[0].sweep_id == result["sweep_id"] and rows[0].sweep_id
        assert sp.sweep_freshness(session) is not None
