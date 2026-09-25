"""#3222：host 级包模式推导与 fleet 视图（derive + 聚合 + gauge 拉取期现算）。"""
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


# ── #3315：gauge 从 host 列拉取期现算；sweep（全量/单机）只写列、永不碰 gauge ──


def _spy_packages_gauge(monkeypatch) -> list[tuple[str, int]]:
    """记录任何对 core 指标对象的 `.set()`：返回 [(mode, value)]。显式把
    PROMETHEUS_AVAILABLE patch 为 True——若有人在 sweep 里重新加回带开关的 set，分支必走到。"""
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


async def _fake_gather_all_package(host_ids, expected):
    # 真实 ack 形态：逐条 package_active，无 package_sha256（见 _e 注释）
    return {
        hid: (True, [{"name": "a", "version": "1.0.0", "exists": True, "ok": True,
                      "package_active": True}], None)
        for hid in host_ids
    }


def _modes_by_host(engine) -> dict[str, object]:
    with sessionmaker(bind=engine)() as session:
        return {h.id: h.script_packages_mode for h in session.execute(select(Host)).scalars()}


async def test_sweep_writes_columns_only_never_the_gauge(db_session, engine, monkeypatch):
    """#3315 根因：sweep 按**本轮切片** set gauge 是病灶（单机 refresh 把 48 打成 1、重启后缺席
    到下次 cron）。修正后两种作用域都只写列——任何 set 都是回退。"""
    _seed_package_plane(db_session, ("h-a", "h-b"))
    calls = _spy_packages_gauge(monkeypatch)
    monkeypatch.setattr(sp, "gather_verify", _fake_gather_all_package)
    factory = sessionmaker(bind=engine)

    await sp.run_sweep(days=30, host_ids=["h-a"], db_factory=factory)
    assert _modes_by_host(engine) == {"h-a": "package", "h-b": None}   # 单机：只动本轮 host 的列

    await sp.run_sweep(days=30, db_factory=factory)
    assert _modes_by_host(engine) == {"h-a": "package", "h-b": "package"}

    assert calls == [], "sweep 不得写 fleet gauge（它由 /metrics 拉取期从列现算）"


def test_metrics_scrape_derives_packages_gauge_from_host_column(client, db_session, monkeypatch):
    """拉取期现算：无需任何 sweep（=进程刚重启）即与 summary `fleet_packages` 同口径——
    退役不计、NULL 计 unknown、四个 mode 全量落值（含 0）。"""
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    now = datetime.now(timezone.utc)
    db_session.add_all([
        Host(id="pm-h1", hostname="pm-h1", status="ONLINE", script_packages_mode="package",
             last_heartbeat=now, created_at=now),
        Host(id="pm-h2", hostname="pm-h2", status="ONLINE", script_packages_mode="package",
             last_heartbeat=now, created_at=now),
        Host(id="pm-h3", hostname="pm-h3", status="ONLINE", script_packages_mode="mixed",
             last_heartbeat=now, created_at=now),
        Host(id="pm-h4", hostname="pm-h4", status="ONLINE",
             last_heartbeat=now, created_at=now),
        Host(id="pm-h5", hostname="pm-h5", status="ONLINE", script_packages_mode="tree",
             retired_at=now, last_heartbeat=now, created_at=now),
    ])
    db_session.commit()

    body = client.get("/metrics").text

    assert 'stability_host_script_packages_mode{mode="package"} 2.0' in body
    assert 'stability_host_script_packages_mode{mode="mixed"} 1.0' in body
    assert 'stability_host_script_packages_mode{mode="unknown"} 1.0' in body
    assert 'stability_host_script_packages_mode{mode="tree"} 0.0' in body   # 退役的 tree 不计
