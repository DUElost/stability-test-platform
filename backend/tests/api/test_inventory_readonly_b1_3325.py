"""阶段 B1 回归测试：四个库存 GET 纯只读、零 N+1 与统一有效状态口径（#3325 / #4117）。

覆盖契约 ``docs/design/2026-11-24-list-pagination-and-facets-contract.md`` §2 & §7.1：
1. 四个库存 GET（``/devices``、``/devices/{id}``、``/hosts``、``/hosts/{id}``）在超时/NULL/存量离线
   夹具下产生 0 次 SQL DML（INSERT/UPDATE/DELETE）与 0 次 Session.commit()；
2. ``GET /devices`` SQL 查询次数不随返回设备数线性增长（消除逐行懒加载 ``device.host`` N+1）；
3. 列表筛选、分页 ``total`` 与返回 ``status`` 共用同一 SQL 有效状态谓词；
4. 新鲜 ``DEGRADED`` 主机不被判离线、无主设备不受主机级联、陈旧度与 VBUS 关机在槽例外同口径生效。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.orm import Session

from backend.models.enums import HostStatus
from backend.models.host import Device, Host


_DML_PREFIXES = ("INSERT", "UPDATE", "DELETE")


class _SqlWriteProbe:
    """记录请求期间的 SQL 语句、DML 次数与 Session.commit() 次数。"""

    def __init__(self, engine):
        self._engine = engine
        self.statements: list[str] = []
        self.dml_statements: list[str] = []
        self.commit_count: int = 0

    def _before_cursor_execute(self, conn, cursor, statement, parameters, context, executemany):
        sql = statement.lstrip()
        self.statements.append(sql)
        if sql.upper().startswith(_DML_PREFIXES):
            self.dml_statements.append(sql)

    def _on_commit(self, session):
        self.commit_count += 1

    def __enter__(self):
        event.listen(self._engine, "before_cursor_execute", self._before_cursor_execute)
        event.listen(Session, "before_commit", self._on_commit)
        return self

    def __exit__(self, exc_type, exc, tb):
        event.remove(self._engine, "before_cursor_execute", self._before_cursor_execute)
        event.remove(Session, "before_commit", self._on_commit)


def _seed_b1_inventory(db_session):
    now = datetime.now(timezone.utc)
    suffix = uuid4().hex[:8]
    fresh_hb = now - timedelta(seconds=30)
    expired_hb = now - timedelta(seconds=600)
    recent_seen = now - timedelta(minutes=10)
    stale_seen = now - timedelta(days=10)

    h_expired = Host(
        id=f"b1-exp-{suffix}",
        hostname=f"b1-exp-{suffix}",
        name=f"b1-exp-{suffix}",
        ip="10.99.1.1",
        status=HostStatus.ONLINE.value,
        last_heartbeat=expired_hb,
        created_at=now,
    )
    h_null = Host(
        id=f"b1-null-{suffix}",
        hostname=f"b1-null-{suffix}",
        name=f"b1-null-{suffix}",
        ip="10.99.1.2",
        status=HostStatus.ONLINE.value,
        last_heartbeat=None,
        created_at=now,
    )
    h_deg_fresh = Host(
        id=f"b1-degf-{suffix}",
        hostname=f"b1-degf-{suffix}",
        name=f"b1-degf-{suffix}",
        ip="10.99.1.3",
        status=HostStatus.DEGRADED.value,
        last_heartbeat=fresh_hb,
        created_at=now,
    )
    h_deg_exp = Host(
        id=f"b1-dege-{suffix}",
        hostname=f"b1-dege-{suffix}",
        name=f"b1-dege-{suffix}",
        ip="10.99.1.4",
        status=HostStatus.DEGRADED.value,
        last_heartbeat=expired_hb,
        created_at=now,
    )
    h_vbus_live = Host(
        id=f"b1-vbus-{suffix}",
        hostname=f"b1-vbus-{suffix}",
        name=f"b1-vbus-{suffix}",
        ip="10.99.1.5",
        status=HostStatus.ONLINE.value,
        last_heartbeat=fresh_hb,
        extra={
            "capacity": {
                "usb_power_slots": {
                    f"B1-VBUS-{suffix}": ["slot-1", "0e8d:2046"],
                },
            },
        },
        created_at=now,
    )
    db_session.add_all([h_expired, h_null, h_deg_fresh, h_deg_exp, h_vbus_live])
    db_session.flush()

    d_exp_recent = Device(
        serial=f"B1-EXP-REC-{suffix}",
        host_id=h_expired.id,
        status="ONLINE",
        last_seen=recent_seen,
        tags=[],
        created_at=now,
    )
    d_exp_stale = Device(
        serial=f"B1-EXP-STALE-{suffix}",
        host_id=h_expired.id,
        status="ONLINE",
        last_seen=stale_seen,
        tags=[],
        created_at=now,
    )
    d_null = Device(
        serial=f"B1-NULL-{suffix}",
        host_id=h_null.id,
        status="BUSY",
        last_seen=recent_seen,
        tags=[],
        created_at=now,
    )
    d_deg_fresh = Device(
        serial=f"B1-DEGF-{suffix}",
        host_id=h_deg_fresh.id,
        status="ONLINE",
        last_seen=recent_seen,
        tags=[],
        created_at=now,
    )
    d_deg_exp = Device(
        serial=f"B1-DEGE-{suffix}",
        host_id=h_deg_exp.id,
        status="ONLINE",
        last_seen=recent_seen,
        tags=[],
        created_at=now,
    )
    d_orphan = Device(
        serial=f"B1-ORPH-{suffix}",
        host_id=None,
        status="ONLINE",
        last_seen=recent_seen,
        tags=[],
        created_at=now,
    )
    d_vbus_off = Device(
        serial=f"B1-VBUS-{suffix}",
        host_id=h_vbus_live.id,
        status="OFFLINE",
        last_seen=stale_seen,
        tags=[],
        created_at=now,
    )
    db_session.add_all(
        [d_exp_recent, d_exp_stale, d_null, d_deg_fresh, d_deg_exp, d_orphan, d_vbus_off]
    )
    db_session.commit()
    return {
        "h_expired": h_expired,
        "h_null": h_null,
        "h_deg_fresh": h_deg_fresh,
        "h_deg_exp": h_deg_exp,
        "h_vbus_live": h_vbus_live,
        "d_exp_recent": d_exp_recent,
        "d_exp_stale": d_exp_stale,
        "d_null": d_null,
        "d_deg_fresh": d_deg_fresh,
        "d_deg_exp": d_deg_exp,
        "d_orphan": d_orphan,
        "d_vbus_off": d_vbus_off,
    }


def test_four_inventory_gets_are_strictly_readonly_zero_dml_zero_commit(
    client, db_session, auth_headers
):
    seed = _seed_b1_inventory(db_session)
    engine = db_session.get_bind()

    endpoints = [
        "/api/v1/devices?include_stale=true&skip=0&limit=50",
        f"/api/v1/devices/{seed['d_exp_recent'].id}",
        "/api/v1/hosts?skip=0&limit=50",
        f"/api/v1/hosts/{seed['h_expired'].id}",
    ]
    for url in endpoints:
        with _SqlWriteProbe(engine) as probe:
            resp = client.get(url, headers=auth_headers)
        assert resp.status_code == 200, (url, resp.text)
        assert probe.dml_statements == [], f"{url} executed DML: {probe.dml_statements}"
        assert probe.commit_count == 0, f"{url} called commit {probe.commit_count} times"

    # 数据库账面行原封不动（证明只读响应投影未改库）
    db_session.expire_all()
    assert db_session.get(Host, seed["h_expired"].id).status == HostStatus.ONLINE.value
    assert db_session.get(Device, seed["d_exp_recent"].id).status == "ONLINE"


def test_effective_status_consistency_across_filter_count_detail_and_stale(
    client, db_session, auth_headers
):
    seed = _seed_b1_inventory(db_session)

    # 1. 有效 OFFLINE 筛选：账面 ONLINE/BUSY 但所属主机超时/NULL 的设备必须命中且 total 对齐
    resp_off = client.get(
        "/api/v1/devices?status=OFFLINE&include_stale=true&skip=0&limit=200",
        headers=auth_headers,
    )
    assert resp_off.status_code == 200
    body_off = resp_off.json()
    off_by_id = {d["id"]: d for d in body_off["items"]}
    assert body_off["total"] == len(body_off["items"])
    for key in ("d_exp_recent", "d_exp_stale", "d_null", "d_deg_exp", "d_vbus_off"):
        dev = seed[key]
        assert dev.id in off_by_id, f"{key} should match effective status=OFFLINE"
        assert off_by_id[dev.id]["status"] == "OFFLINE"
    assert off_by_id[seed["d_exp_stale"].id]["is_stale"] is True
    assert off_by_id[seed["d_exp_recent"].id]["is_stale"] is False

    # 2. 有效 ONLINE 筛选：新鲜 DEGRADED 与无主设备保持 ONLINE；过期主机下设备不得混入
    resp_on = client.get(
        "/api/v1/devices?status=ONLINE&include_stale=true&skip=0&limit=200",
        headers=auth_headers,
    )
    assert resp_on.status_code == 200
    on_ids = {d["id"] for d in resp_on.json()["items"]}
    assert seed["d_deg_fresh"].id in on_ids
    assert seed["d_orphan"].id in on_ids
    assert seed["d_exp_recent"].id not in on_ids
    assert seed["d_deg_exp"].id not in on_ids

    # 3. 默认 include_stale=false：有效离线且超 7 天的 d_exp_stale 被隐藏，但 VBUS 关机在槽 d_vbus_off 保留
    resp_default = client.get("/api/v1/devices?skip=0&limit=200", headers=auth_headers)
    assert resp_default.status_code == 200
    default_ids = {d["id"] for d in resp_default.json()["items"]}
    assert seed["d_exp_stale"].id not in default_ids
    assert seed["d_exp_recent"].id in default_ids
    assert seed["d_vbus_off"].id in default_ids

    # 4. 主机列表/详情：新鲜 DEGRADED 不被判离线，过期/NULL 投影为 OFFLINE
    resp_hosts = client.get("/api/v1/hosts?skip=0&limit=200", headers=auth_headers)
    assert resp_hosts.status_code == 200
    hosts_by_id = {h["id"]: h for h in resp_hosts.json()["items"]}
    assert hosts_by_id[seed["h_deg_fresh"].id]["status"] == HostStatus.DEGRADED.value
    assert hosts_by_id[seed["h_deg_exp"].id]["status"] == HostStatus.OFFLINE.value
    assert hosts_by_id[seed["h_null"].id]["status"] == HostStatus.OFFLINE.value
    assert hosts_by_id[seed["h_expired"].id]["status"] == HostStatus.OFFLINE.value

    resp_deg = client.get(f"/api/v1/hosts/{seed['h_deg_fresh'].id}", headers=auth_headers)
    assert resp_deg.status_code == 200
    assert resp_deg.json()["status"] == HostStatus.DEGRADED.value


def test_list_devices_sql_count_is_constant_no_host_n_plus_1(
    client, db_session, auth_headers
):
    """返回设备行数从 2 增至 12 时，SQL 语句数不得随行数增长（消除 device.host 懒加载 N+1）。"""
    now = datetime.now(timezone.utc)
    suffix = uuid4().hex[:8]
    engine = db_session.get_bind()

    def _add_hosts_and_devices(count: int, tag: str) -> None:
        for i in range(count):
            hid = f"n1-{tag}-{i}-{suffix}"
            db_session.add(
                Host(
                    id=hid,
                    hostname=hid,
                    name=hid,
                    ip=f"10.88.{1 if tag == 'a' else 2}.{i + 1}",
                    status=HostStatus.ONLINE.value,
                    last_heartbeat=now - timedelta(seconds=20),
                    created_at=now,
                )
            )
            db_session.add(
                Device(
                    serial=f"N1-{tag}-{i}-{suffix}",
                    host_id=hid,
                    status="ONLINE",
                    last_seen=now,
                    tags=[],
                    created_at=now,
                )
            )
        db_session.commit()

    _add_hosts_and_devices(2, "a")
    with _SqlWriteProbe(engine) as probe_small:
        r1 = client.get("/api/v1/devices?skip=0&limit=50", headers=auth_headers)
    assert r1.status_code == 200
    assert probe_small.dml_statements == []

    _add_hosts_and_devices(10, "b")
    with _SqlWriteProbe(engine) as probe_large:
        r2 = client.get("/api/v1/devices?skip=0&limit=50", headers=auth_headers)
    assert r2.status_code == 200
    assert len(r2.json()["items"]) >= len(r1.json()["items"]) + 10
    assert probe_large.dml_statements == []
    assert len(probe_large.statements) == len(probe_small.statements), (
        f"SQL count grew with rows: small={len(probe_small.statements)} "
        f"large={len(probe_large.statements)}"
    )
