"""#3222：host 级包模式推导与 fleet 视图（derive + 聚合 + gauge 作用域）。"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.core import metrics as core_metrics
from backend.models.host import Host
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun, PlanRunHost
from backend.models.script import Script
from backend.services import script_presence as sp
from backend.services.script_presence import derive_packages_mode, fleet_packages_mode


def _e(name, active):
    # 真实 ack 形态（script_verifier.verify_scripts_payload）：不回传 package_sha256，
    # 带身份判据在控制面 expected 侧——夹具必须镜像这一形态，曾经的版本塞了
    # package_sha256 进 ack，测试绿而生产 48 台全 unknown（2026-09-25 首采当场暴露）。
    return {"name": name, "version": "1.0.0", "ok": True, "package_active": active}


def test_derive_matrix():
    K = {("a", "1.0.0"), ("b", "1.0.0")}
    assert derive_packages_mode([], K, reachable_empty=True) is None
    assert derive_packages_mode([], K, reachable_empty=False) is None
    assert derive_packages_mode([_e("a", True)], set(), False) is None            # expected 全无包身份 → unknown
    assert derive_packages_mode([_e("a", True), _e("b", True)], K, False) == "package"
    assert derive_packages_mode([_e("a", False)], K, False) == "tree"
    assert derive_packages_mode([_e("a", True), _e("b", False)], K, False) == "mixed"
    # ack 里有行但都不在 expected 带身份集（老 agent 未回 package_active 等情形）→ unknown 不误报
    assert derive_packages_mode([_e("c", True)], K, False) is None
    # 可达集内部分目标带包身份：只判带身份的行，不误伤
    assert derive_packages_mode([_e("a", True), _e("z", False)], K, False) == "package"


def test_fleet_packages_mode_counts(db_session: Session):
    now = datetime.now(timezone.utc)
    def h(hid, **kw):
        return Host(id=hid, hostname=hid, ip=f"10.0.0.{hid[-1]}", ip_address=f"10.0.0.{hid[-1]}",
                    status="ONLINE", last_heartbeat=now, created_at=now, **kw)
    db_session.add_all([
        h("h1", script_packages_mode="package"),
        h("h2", script_packages_mode="package"),
        h("h3", script_packages_mode="mixed"),
        h("h4"),
        h("h5", script_packages_mode="tree", retired_at=now),  # 退役主机不计
    ])
    db_session.commit()
    assert fleet_packages_mode(db_session) == {"package": 2, "tree": 0, "mixed": 1, "unknown": 1}


# ── #3315：gauge 出口的作用域混淆（fleet 聚合不得被单机 refresh 的部分集覆盖）──


def _spy_packages_gauge(monkeypatch) -> list[tuple[str, int]]:
    """记录每次 `.set()`：返回 [(mode, value)]。prometheus 在测试环境可能未装，
    显式把 PROMETHEUS_AVAILABLE 与 gauge 对象一起 patch 到真走指标分支。"""
    calls: list[tuple[str, int]] = []

    class _GaugeSpy:
        def labels(self, *, mode):
            class _Set:
                def set(self, value):
                    calls.append((mode, int(value)))
            return _Set()

    monkeypatch.setattr(core_metrics, "PROMETHEUS_AVAILABLE", True)
    monkeypatch.setattr(core_metrics, "host_script_packages_mode", _GaugeSpy())
    return calls


def _seed_package_plane(db_session, host_ids: tuple[str, ...]):
    """两 host × 一个带包身份的 active 版本，经 observed run 可达——derive 能出 package。"""
    now = datetime.now(timezone.utc)
    for hid in host_ids:
        db_session.add(Host(id=hid, hostname=hid, status="ONLINE"))
    db_session.add(Script(
        name="a", version="1.0.0", script_type="test",
        nfs_path="/opt/agent/scripts/a/a.py", content_sha256="0" * 64,
        package_sha256="c" * 64, is_active=True,
    ))
    plan = Plan(name="p-mode")
    db_session.add(plan)
    db_session.flush()
    db_session.add(PlanStep(
        plan_id=plan.id, step_key="s1", script_name="a", script_version="1.0.0",
        stage="init", enabled=True,
    ))
    run = PlanRun(
        plan_id=plan.id, plan_snapshot={"steps": []}, run_type="MANUAL",
        started_at=now,
    )
    db_session.add(run)
    db_session.flush()
    for hid in host_ids:
        db_session.add(PlanRunHost(plan_run_id=run.id, host_id=hid))
    db_session.commit()


async def test_full_sweep_sets_fleet_gauge_with_host_count(db_session, engine, monkeypatch):
    """全量作用域：gauge 聚合 = 全体 host 计数（这是「package == hosts_total」对账的前提）。"""
    _seed_package_plane(db_session, ("h-a", "h-b"))
    calls = _spy_packages_gauge(monkeypatch)

    async def fake_gather(host_ids, expected):
        return {
            hid: (True, [{"name": "a", "version": "1.0.0", "exists": True, "ok": True,
                          "package_active": True}], None)
            for hid in host_ids
        }

    monkeypatch.setattr(sp, "gather_verify", fake_gather)
    await sp.run_sweep(days=30, db_factory=sessionmaker(bind=engine))

    assert dict(calls) == {"package": 2, "tree": 0, "mixed": 0, "unknown": 0}


async def test_host_scoped_sweep_writes_column_but_not_gauge(db_session, engine, monkeypatch):
    """#3315 回归：单机 refresh（run_sweep(host_ids=[…])）只写列——gauge 是 fleet 聚合，
    拿 1 台切片 Counter 去 set 会把 48 打成 1（2026-09-25 首采当天实测被覆盖）。"""
    _seed_package_plane(db_session, ("h-a", "h-b"))
    calls = _spy_packages_gauge(monkeypatch)

    async def fake_gather(host_ids, expected):
        return {
            hid: (True, [{"name": "a", "version": "1.0.0", "exists": True, "ok": True,
                          "package_active": True}], None)
            for hid in host_ids
        }

    monkeypatch.setattr(sp, "gather_verify", fake_gather)
    await sp.run_sweep(days=30, host_ids=["h-a"], db_factory=sessionmaker(bind=engine))

    assert calls == [], "单机作用域绝不得覆盖 fleet gauge"
    factory = sessionmaker(bind=engine)
    with factory() as session:
        hosts = session.execute(select(Host)).scalars().all()
        by_id = {x.id: x for x in hosts}
    assert by_id["h-a"].script_packages_mode == "package"   # 本轮 host 的列照常写
    assert by_id["h-b"].script_packages_mode is None        # 未 sweep 的 host 保持 unknown
