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

    # 顶层导入后 patch 点必须在**使用方**的命名空间（服务模块）
    monkeypatch.setattr(sp, "gather_verify", fake_gather)
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


# ── #3111：账本**覆盖边界**（active 但无 Plan 引用的版本）────────────────────


def test_active_unreferenced_versions_is_complement_of_full_target_set():
    """两侧互补：`|active| = |full| + |uncovered|`，交集为空，停用版本两侧都不进。

    只测一侧会让「两边用了不同的 referenced 口径」逃逸，所以要双向对拍。
    """
    script_rows = _script_rows(
        ("a", "1.0.0", {}), ("b", "2.0.0", {}), ("c", "3.0.0", {}),
    )
    script_rows.append({**_script_rows(("d", "4.0.0", {}))[0], "is_active": False})
    step_rows = [{"plan_id": 1, "script_name": "a", "script_version": "1.0.0"}]

    full = sp.build_full_target_set(step_rows, script_rows)
    uncovered = sp.active_unreferenced_versions(step_rows, script_rows)

    assert full == [("a", "1.0.0")]
    assert uncovered == [("b", "2.0.0"), ("c", "3.0.0")]        # 停用的 d 不计入
    active = {(str(r["name"]), str(r["version"])) for r in script_rows if r["is_active"]}
    assert set(full) | set(uncovered) == active
    assert not (set(full) & set(uncovered))


def test_active_unreferenced_versions_catches_newly_merged_unreferenced_version():
    """#3085 形态：新版本已 active、尚无 Plan 引用 → 必须落在覆盖差集里。

    反向自证：把它挂进一个启用步骤后必须**从差集消失**（否则这个面只是摆设），
    并同时进全集——两侧一起动才说明它们真的由同一对集合派生。
    """
    script_rows = _script_rows(("fill_storage", "1.1.0", {}), ("fill_storage", "1.1.1", {}))

    assert sp.active_unreferenced_versions([], script_rows) == [
        ("fill_storage", "1.1.0"), ("fill_storage", "1.1.1"),
    ]

    steps = [{"plan_id": 7, "script_name": "fill_storage", "script_version": "1.1.1"}]
    assert sp.active_unreferenced_versions(steps, script_rows) == [("fill_storage", "1.1.0")]
    assert sp.build_full_target_set(steps, script_rows) == [("fill_storage", "1.1.1")]


async def test_run_sweep_reports_uncovered_active_without_writing_rows(
    db_session, engine, monkeypatch, caplog
):
    """未覆盖的 active 版本**不落行**，由计数与日志承担（#3111）。

    这条同时钉住两件容易做反的事：① 未覆盖 ≠ 缺口，不得往五态里塞 missing；
    ② 它必须出现在 sweep 结果与日志里，否则「账本不核验它」这件事又回到不可见。
    """
    db_session.add(Host(id="h-unc", hostname="h-unc", status="ONLINE"))
    db_session.add(Script(
        name="fill_storage", version="1.1.1", script_type="test",
        nfs_path="/opt/agent/scripts/fill_storage/v1.1.1/fill_storage.py",
        content_sha256="0" * 64, is_active=True,
    ))
    db_session.commit()

    calls: list[list[str]] = []

    async def fake_gather(host_ids, expected):
        calls.append(list(host_ids))
        return {hid: (True, [], None) for hid in host_ids}

    monkeypatch.setattr(sp, "gather_verify", fake_gather)
    factory = sessionmaker(bind=engine)
    with caplog.at_level("INFO", logger="backend.services.script_presence"):
        result = await sp.run_sweep(days=30, db_factory=factory)

    assert result["full_versions"] == 0
    assert result["uncovered_active_versions"] == 1
    assert calls == [], "无 Plan 引用 ⇒ 无可达集 ⇒ 不发 RPC"
    assert result["counts"][sp.STATE_MISSING] == 0, "未覆盖不得折成 missing（那是假缺口）"
    with factory() as session:
        rows = session.execute(select(HostScriptPresence)).scalars().all()
        assert rows == [], "未覆盖版本一样不落行——账本对它确实无话可说"
    assert any("script_presence_uncovered_active" in r.message for r in caplog.records), (
        "覆盖差集必须进日志，否则这个面只在 API 里、日 sweep 时无人看见"
    )


# ── #3135：逐条失败不得塌成整片 unknown ─────────────────────────────────────

def test_classify_per_entry_failure_is_not_collapsed_to_unknown():
    """#3135 回归：`sha_mismatch` 是「逐条结果可用」的信号，必须逐条落 missing/mismatch。

    修前：`not verify_ok` 一刀切 ⇒ 整机可达目标全 unknown（实测一台机 28 个 unknown，
    而真因只是 `clear_recents@1.0.4` 一个 sha 不符）——缺口面因此失效。
    """
    reachable = set(FULL)
    states = sp.classify_host_presence(
        host_id="h1", full=FULL, reachable=reachable, in_maintenance=False,
        verify_ok=False,                      # gather_verify 的 all_ok：有一条不过就是 False
        verify_error="sha_mismatch",          # 但逐条结果在 verify_entries 里
        verify_entries=[
            {"name": "a", "version": "1.0.0", "ok": True, "exists": True},
            {"name": "b", "version": "2.0.0", "ok": False, "exists": True,
             "error": "support_file_mismatch:_adb.py"},
        ],
    )
    assert states[("a", "1.0.0")] == (sp.STATE_PRESENT, "")          # 好的仍然是绿
    assert states[("b", "2.0.0")][0] == sp.STATE_MISMATCH            # 坏的落进缺口面
    assert all(s != sp.STATE_UNKNOWN for s, _d in states.values())


def test_classify_unreachable_error_still_marks_whole_host_unknown():
    """反向护栏：RPC 级失败（没拿到结果）仍必须整机 unknown——不能被 #3135 的放宽带走。"""
    for err in ("agent_offline", "rpc_failed: timeout", "verify_exception: boom"):
        states = sp.classify_host_presence(
            host_id="h1", full=[("a", "1.0.0")], reachable={("a", "1.0.0")},
            in_maintenance=False, verify_ok=False, verify_entries=[], verify_error=err,
        )
        assert states[("a", "1.0.0")] == (sp.STATE_UNKNOWN, err)


def test_classify_sha_mismatch_without_entries_still_unknown_not_green():
    """没拿到任何逐条结果 + 非不可达错误 → 只能如实 unknown（不猜 present/missing）。"""
    states = sp.classify_host_presence(
        host_id="h1", full=[("a", "1.0.0")], reachable={("a", "1.0.0")},
        in_maintenance=False, verify_ok=False, verify_entries=[], verify_error="sha_mismatch",
    )
    assert states[("a", "1.0.0")] == (sp.STATE_UNKNOWN, "sha_mismatch")


# ── #3089：账本差集清理 ────────────────────────────────────────────────────

async def test_run_sweep_removes_orphans_full_scope(db_session, engine, monkeypatch):
    """全量 sweep 必须删掉「不在本轮生成集」的行：退役 host 的整行 + 旧目标版本的残留。"""
    live = Host(id="h-live", hostname="h-live", status="ONLINE")
    retired = Host(id="h-retired", hostname="h-retired", status="OFFLINE")
    retired.retired_at = datetime.now(timezone.utc)
    db_session.add_all([live, retired])
    db_session.add(Script(
        name="a", version="1.0.0", script_type="test",
        nfs_path="/opt/agent/scripts/a/v1.0.0/a.py", content_sha256="0" * 64, is_active=True,
    ))
    plan = Plan(name="p-orphan")
    db_session.add(plan)
    db_session.flush()
    db_session.add(PlanStep(plan_id=plan.id, step_key="s1", script_name="a",
                            script_version="1.0.0", stage="init", enabled=True))
    run = PlanRun(plan_id=plan.id, plan_snapshot={"steps": []}, run_type="MANUAL",
                  started_at=datetime.now(timezone.utc))
    db_session.add(run)
    db_session.flush()
    db_session.add(PlanRunHost(plan_run_id=run.id, host_id=live.id))
    # 孤儿①：退役 host 的行（旧 sweep_id）
    # 孤儿②：live host 上「已不在目标集」的旧版本行
    db_session.add_all([
        HostScriptPresence(host_id=retired.id, name="a", version="1.0.0", state="present",
                           detail="", checked_at=datetime.now(timezone.utc) - timedelta(days=5),
                           sweep_id="old-sweep"),
        HostScriptPresence(host_id=live.id, name="gone_script", version="9.9.9", state="mismatch",
                           detail="", checked_at=datetime.now(timezone.utc) - timedelta(days=5),
                           sweep_id="old-sweep"),
    ])
    db_session.commit()

    async def fake_gather(host_ids, expected):
        return {hid: (True, [{"name": "a", "version": "1.0.0", "exists": True, "ok": True}],
                       None) for hid in host_ids}

    monkeypatch.setattr(sp, "gather_verify", fake_gather)
    factory = sessionmaker(bind=engine)
    result = await sp.run_sweep(days=30, db_factory=factory)

    assert result["orphans_removed"] == 2
    with factory() as session:
        rows = session.execute(select(HostScriptPresence)).scalars().all()
        keys = {(r.host_id, r.name, r.version) for r in rows}
        assert (retired.id, "a", "1.0.0") not in keys          # 退役 host 整行清掉
        assert (live.id, "gone_script", "9.9.9") not in keys   # 旧目标版本清掉
        assert (live.id, "a", "1.0.0") in keys                 # 本轮行留下
        # 新鲜度不再被历史行钉死
        assert sp.sweep_freshness(session) >= datetime.now(timezone.utc) - timedelta(minutes=5)


async def test_run_sweep_host_scoped_touches_only_that_host(db_session, engine, monkeypatch):
    """单机 refresh 只清该 host 的孤儿行——绝不触碰别的 host（否则一次刷新清空全表）。"""
    h1 = Host(id="h-one", hostname="h-one", status="ONLINE")
    h2 = Host(id="h-two", hostname="h-two", status="ONLINE")
    db_session.add_all([h1, h2])
    db_session.commit()
    old = datetime.now(timezone.utc) - timedelta(days=3)
    db_session.add_all([
        HostScriptPresence(host_id="h-one", name="a", version="1.0.0", state="present",
                           detail="", checked_at=old, sweep_id="old-sweep"),
        HostScriptPresence(host_id="h-two", name="b", version="2.0.0", state="present",
                           detail="", checked_at=old, sweep_id="old-sweep"),
    ])
    db_session.commit()

    async def fake_gather(host_ids, expected):
        return {hid: (True, [], None) for hid in host_ids}

    monkeypatch.setattr(sp, "gather_verify", fake_gather)
    factory = sessionmaker(bind=engine)
    result = await sp.run_sweep(days=30, host_ids=["h-one"], db_factory=factory)

    with factory() as session:
        rows = session.execute(select(HostScriptPresence)).scalars().all()
        kept = {(r.host_id, r.name, r.version) for r in rows}
        assert ("h-one", "a", "1.0.0") not in kept     # h-one 的旧行被本轮差集清掉
        assert ("h-two", "b", "2.0.0") in kept         # 别的 host 原样保留
        assert all(r.host_id == "h-two" for r in rows)  # 且本轮没给 h-two 写任何行
    assert result["orphans_removed"] == 1
