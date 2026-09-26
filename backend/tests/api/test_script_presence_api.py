"""#2958 在位矩阵 API：形状、六态计数、新鲜度与单机按需刷新。

锁五件事：
① 没跑过 sweep（无行）时 `stale=True` 且计数全 0——**账本不存在不得当绿读**；
② 有行时六态计数、缺口 host 去重、新鲜度取 min(checked_at)；
③ 单机矩阵只回非 ``n_a`` 项（n/a 是「不该有」不是「缺」，塞进 items 会误导 UI）；
④ `POST /refresh` 是**单机**作用域：只对该 host 发 RPC 并只写它的行；
⑤ 汇总必须自报**覆盖边界**（#3111）：`full_versions` 之外还有多少个 active 版本账本
   根本不核验——`missing/mismatch = 0` 只覆盖前者。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.models.host import Host
from backend.models.plan import Plan, PlanStep
from backend.models.script import Script
from backend.models.script_presence import HostScriptPresence
from backend.services import script_presence as sp


def _seed_host(db_session, host_id: str = "h-api") -> Host:
    host = Host(id=host_id, hostname=host_id, status="ONLINE")
    db_session.add(host)
    db_session.flush()
    return host


def _presence(db_session, host_id: str, name: str, version: str, state: str,
              *, age_hours: float = 0.0) -> None:
    db_session.add(HostScriptPresence(
        host_id=host_id, name=name, version=version, state=state, detail="",
        checked_at=datetime.now(timezone.utc) - timedelta(hours=age_hours),
        sweep_id="sweep-test",
    ))


def test_summary_exposes_fleet_packages_view(client, db_session, admin_headers):
    """#3222：summary 必含 fleet_packages（fleet 包模式机器视图，ADR-0051 strict 不变量的出口）。"""
    r = client.get("/api/v1/script-presence/summary", headers=admin_headers)
    data = r.json()["data"]
    assert data["fleet_packages"] == {"package": 0, "tree": 0, "mixed": 0, "unknown": 0}


def test_summary_without_rows_is_stale_not_green(client, db_session, admin_headers):
    r = client.get("/api/v1/script-presence/summary", headers=admin_headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["stale"] is True
    assert data["counts"] == {
        "present": 0, "missing": 0, "mismatch": 0,
        "unknown": 0, "n_a": 0, "maintenance": 0,
    }


def test_summary_counts_gap_hosts_and_freshness(client, db_session, admin_headers):
    _seed_host(db_session)
    _presence(db_session, "h-api", "monkey_setup", "2.3.9", "present")
    _presence(db_session, "h-api", "check_device", "1.0.2", "missing", age_hours=30)
    _presence(db_session, "h-api", "gpu_setup", "1.2.2", "mismatch")
    _presence(db_session, "h-api", "sleep_setup", "1.0.0", "n_a")
    db_session.commit()

    r = client.get("/api/v1/script-presence/summary", headers=admin_headers)
    data = r.json()["data"]
    assert data["counts"]["present"] == 1
    assert data["counts"]["missing"] == 1 and data["counts"]["mismatch"] == 1
    assert data["hosts_with_gap"] == 1
    assert data["hosts_total"] == 1
    # 新鲜度取 min（最旧一行 30h 前）→ 未超 48h 阈值，不算 stale
    assert data["stale"] is False
    assert data["checked_at_min"] < data["checked_at_max"]


def test_summary_reports_uncovered_active_versions(client, db_session, admin_headers):
    """#3111 覆盖边界：active 但无 Plan 引用的版本不落账本，由计数如实报出。

    反向自证：把该版本挂进一个**启用**步骤后，它必须同时离开 uncovered、进入
    full——两侧一起动才说明这两个数是同一对集合派生的（只测一侧会漏掉口径分叉）。
    """
    _seed_host(db_session)
    for name, version in (("fill_storage", "1.1.1"), ("check_device", "1.0.2")):
        db_session.add(Script(
            name=name, version=version, script_type="test",
            nfs_path=f"/opt/agent/scripts/{name}/v{version}/{name}.py",
            content_sha256="0" * 64, is_active=True,
        ))
    plan = Plan(name="p-coverage")
    db_session.add(plan)
    db_session.flush()
    db_session.add(PlanStep(
        plan_id=plan.id, step_key="s1", script_name="check_device",
        script_version="1.0.2", stage="init", enabled=True,
    ))
    db_session.commit()

    data = client.get("/api/v1/script-presence/summary", headers=admin_headers).json()["data"]
    assert data["full_versions"] == 1
    assert data["uncovered_active_versions"] == 1     # fill_storage 1.1.1
    # 反向自证：账本里没有它的行 ⇒「未覆盖」不是文档话术，是账本确实无话可说
    assert db_session.query(HostScriptPresence).filter_by(name="fill_storage").count() == 0

    # 把它也挂进启用步骤 → 两侧同时翻转
    db_session.add(PlanStep(
        plan_id=plan.id, step_key="s2", script_name="fill_storage",
        script_version="1.1.1", stage="init", enabled=True,
    ))
    db_session.commit()
    data = client.get("/api/v1/script-presence/summary", headers=admin_headers).json()["data"]
    assert data["full_versions"] == 2
    assert data["uncovered_active_versions"] == 0


def test_host_detail_404_and_excludes_na(client, db_session, admin_headers):
    r = client.get("/api/v1/script-presence/hosts/nope", headers=admin_headers)
    assert r.status_code == 404

    _seed_host(db_session)
    _presence(db_session, "h-api", "monkey_setup", "2.3.9", "present")
    _presence(db_session, "h-api", "mtbf_setup", "1.0.0", "n_a")
    db_session.commit()

    r = client.get("/api/v1/script-presence/hosts/h-api", headers=admin_headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert [i["name"] for i in data["items"]] == ["monkey_setup"]   # n_a 不进 items
    assert data["counts"]["n_a"] == 1
    assert data["host_id"] == "h-api"


async def test_refresh_is_host_scoped(client, db_session, admin_headers, monkeypatch):
    _seed_host(db_session, "h-refresh")
    _seed_host(db_session, "h-other")
    db_session.commit()

    calls: list[list[str]] = []

    async def fake_gather(host_ids, expected):
        calls.append(list(host_ids))
        return {hid: (True, [], None) for hid in host_ids}

    # 目标集为空（无 plan_step）→ sweep 不发 RPC，但该机行照写（如实暴露「无目标」）
    monkeypatch.setattr(sp, "gather_verify", fake_gather)
    r = client.post("/api/v1/script-presence/refresh?host_id=h-refresh", headers=admin_headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["host_id"] == "h-refresh" and data["sweep_id"]
    assert calls == []                       # 无目标 → 不发 RPC（省一次往返）

    r2 = client.post("/api/v1/script-presence/refresh?host_id=nope", headers=admin_headers)
    assert r2.status_code == 404


# ── #3091：写端点必须 require_admin ────────────────────────────────────────

def test_refresh_requires_admin(client, db_session, auth_headers):
    """非管理员触发 refresh 必须 403（写端点：agent RPC + 账本 upsert，与同域写端点一致）。"""
    _seed_host(db_session, "h-admin")
    db_session.commit()
    r = client.post("/api/v1/script-presence/refresh?host_id=h-admin", headers=auth_headers)
    assert r.status_code == 403


def test_refresh_retired_host_is_404(client, db_session, admin_headers):
    """#3089：退役 host 不在账本射程 → 明确 4xx（此前 200 + 全零是静默空转）。"""
    host = _seed_host(db_session, "h-retired-presence")
    host.retired_at = datetime.now(timezone.utc)
    db_session.commit()
    r = client.post("/api/v1/script-presence/refresh?host_id=h-retired-presence",
                    headers=admin_headers)
    assert r.status_code == 404
    assert "retired" in r.json()["detail"]


# ── #3333①：POST /refresh-all（全量按需重采，admin + 节流）──────────────────

def _reset_refresh_all_throttle(monkeypatch):
    """节流是进程内模块态——逐用例复位，避免用例间互相 429。"""
    from backend.api.routes import script_presence as routes

    monkeypatch.setattr(routes, "_last_refresh_all_monotonic", None)
    return routes


def test_refresh_all_requires_admin(client, db_session, auth_headers, monkeypatch):
    """非管理员触发全量重采必须 403（写端点：48 台 verify RPC + 整轮 upsert）。"""
    _reset_refresh_all_throttle(monkeypatch)
    r = client.post("/api/v1/script-presence/refresh-all", headers=auth_headers)
    assert r.status_code == 403


def test_refresh_all_runs_full_sweep_and_returns_summary(
    client, db_session, admin_headers, monkeypatch
):
    """admin 触发 → 在 backend 进程内跑**全量** run_sweep 并回汇总（不是单机）。"""
    _reset_refresh_all_throttle(monkeypatch)
    calls: list[dict] = []

    async def fake_run_sweep(**kwargs):
        calls.append(kwargs)
        return {
            "sweep_id": "sw-full-1", "hosts": 48, "hosts_verified": 48,
            "full_versions": 51, "uncovered_active_versions": 47,
            "rows": 2496, "orphans_removed": 0,
            "counts": {state: (1 if state == sp.STATE_PRESENT else 0)
                       for state in sp.PRESENCE_STATES},
        }

    monkeypatch.setattr(sp, "run_sweep", fake_run_sweep)
    r = client.post("/api/v1/script-presence/refresh-all", headers=admin_headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["sweep_id"] == "sw-full-1"
    assert data["hosts"] == 48 and data["rows"] == 2496
    assert data["counts"]["present"] == 1
    assert data["counts"]["missing"] == 0
    # 全量语义：调用不带 host_ids（= 全 scope）；days 取历史窗口默认值
    assert calls and "host_ids" not in calls[0]
    assert calls[0]["days"] >= 1


def test_refresh_all_is_throttled(client, db_session, admin_headers, monkeypatch):
    """节流：60s 内的第二次发起 → 429（全量 sweep 不能连点叠加）。"""
    routes = _reset_refresh_all_throttle(monkeypatch)

    async def fake_run_sweep(**kwargs):
        return {"sweep_id": "sw-t", "hosts": 0, "rows": 0,
                "counts": {state: 0 for state in sp.PRESENCE_STATES}}

    monkeypatch.setattr(sp, "run_sweep", fake_run_sweep)
    first = client.post("/api/v1/script-presence/refresh-all", headers=admin_headers)
    assert first.status_code == 200
    second = client.post("/api/v1/script-presence/refresh-all", headers=admin_headers)
    assert second.status_code == 429
    assert "节流" in second.json()["detail"]

    # 节流窗过去后放行（时间戳回拨到窗口外模拟）
    monkeypatch.setattr(
        routes, "_last_refresh_all_monotonic",
        routes._last_refresh_all_monotonic - routes.REFRESH_ALL_MIN_INTERVAL_SECONDS - 1,
    )
    third = client.post("/api/v1/script-presence/refresh-all", headers=admin_headers)
    assert third.status_code == 200


def test_run_sweep_refusal_is_not_swallowed_into_a_200(
    client, db_session, admin_headers, monkeypatch
):
    """#3333② 的 API 侧形状：就绪面失效时 `SweepNotReadyError` **穿透**，不得被吞成静默 200。

    （正常路径不会发生——backend 进程 `create_sio_server` 已就绪；这条防的是以后有人
    给它包一层 try/except 把拒跑做成「形似成功」，那正是本单要消除的形态。）
    """
    _reset_refresh_all_throttle(monkeypatch)

    async def refusing_run_sweep(**kwargs):
        raise sp.SweepNotReadyError("not a backend process")

    monkeypatch.setattr(sp, "run_sweep", refusing_run_sweep)
    with pytest.raises(sp.SweepNotReadyError):
        client.post("/api/v1/script-presence/refresh-all", headers=admin_headers)
