"""阶段 B2-2 验收测试：Agent host 增量筛选/全局排序、独立 10 键 Facets 与跨页选择预览（#496 / #4117）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.orm import Session

from backend.api.routes import hosts as hosts_route
from backend.models.host import Host

DESIRED_DIGEST = "sha256:" + "a" * 64
DRIFT_DIGEST = "sha256:" + "b" * 64


def _make_host(
    db: Session,
    *,
    host_id: str,
    name: str,
    ip: str,
    status: str = "ONLINE",
    last_heartbeat: datetime | None = None,
    set_heartbeat_none: bool = False,
    retired_at: datetime | None = None,
    emptied_at: datetime | None = None,
    agent_artifact_digest: str | None = None,
    extra: dict | None = None,
    mount_status: dict | None = None,
) -> Host:
    now = datetime.now(timezone.utc)
    hb = None if set_heartbeat_none else (last_heartbeat if last_heartbeat is not None else now)
    host = Host(
        id=host_id,
        hostname=name,
        name=name,
        ip=ip,
        ip_address=ip,
        status=status,
        last_heartbeat=hb,
        retired_at=retired_at,
        emptied_at=emptied_at,
        agent_artifact_digest=agent_artifact_digest,
        extra={} if extra is None else extra,
        mount_status={} if mount_status is None else mount_status,
    )
    db.add(host)
    db.flush()
    return host


def _attach_readonly_probe(db: Session):
    dml_stmts: list[str] = []
    commits: list[int] = []
    engine = db.get_bind()

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        upper = statement.lstrip().upper()
        if upper.startswith(("INSERT", "UPDATE", "DELETE")):
            dml_stmts.append(statement)

    def _before_commit(session):
        commits.append(1)

    event.listen(engine, "before_cursor_execute", _before_cursor_execute)
    event.listen(Session, "before_commit", _before_commit)

    def _cleanup():
        event.remove(engine, "before_cursor_execute", _before_cursor_execute)
        event.remove(Session, "before_commit", _before_commit)

    return dml_stmts, commits, _cleanup


class TestHostFacetsAndSyncPredicateParity:
    """GET /api/v1/hosts/facets 10 键口径 + upgrade_pending 快捷筛选同源性 + 零写入。"""

    def test_facets_10_keys_and_upgrade_pending_matches_list_filter(
        self, client, db_session, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(hosts_route, "_list_desired_artifact_digest", lambda: DESIRED_DIGEST)
        prefix = f"b22-{uuid4().hex[:6]}"
        now = datetime.now(timezone.utc)

        # 清理或按差值验证：为保证测试对并发夹具零污染，先读取当前基线 facets
        base = client.get("/api/v1/hosts/facets", headers=auth_headers).json()["data"]

        # 1) ONLINE + installed + matched + 健康 + 挂载正常
        h_matched = _make_host(
            db_session,
            host_id=f"{prefix}-01",
            name=f"NodeMatched-{prefix}",
            ip=f"10.71.1.{int(uuid4().hex[:2], 16) % 200 + 10}",
            status="ONLINE",
            agent_artifact_digest=DESIRED_DIGEST,
            extra={
                "agent_installed": True,
                "agent_version": "2.1.0",
                "agent_code_revision": "rev111",
                "health": {"status": "HEALTHY", "reasons": []},
                "capacity": {"online_healthy_devices": 8},
                "cpu_load": 20.0,
            },
            mount_status={"/mnt/center": {"ok": True}},
        )
        # 2) ONLINE + installed + drift (= upgrade_pending) + 挂载异常 + UNSCHEDULABLE + USB P3
        h_drift = _make_host(
            db_session,
            host_id=f"{prefix}-02",
            name=f"NodeDrift-{prefix}",
            ip=f"10.71.2.{int(uuid4().hex[:2], 16) % 200 + 10}",
            status="ONLINE",
            agent_artifact_digest=DRIFT_DIGEST,
            extra={
                "agent_installed": True,
                "agent_version": "2.0.0",
                "agent_code_revision": "rev000",
                "health": {"status": "UNSCHEDULABLE", "reasons": ["usb_host_controller_dead", "mount_failed"]},
                "capacity": {"online_healthy_devices": 0},
                "cpu_load": 85.0,
            },
            mount_status={"/mnt/center": {"ok": False}},
        )
        # 3) ONLINE + installed + unknown (无 digest) -> 留在 trackable 分母中，不计 aligned/upgrade_pending；
        #    mount_status 为空 dict -> 不得计为 mount_anomaly
        h_unknown = _make_host(
            db_session,
            host_id=f"{prefix}-03",
            name=f"NodeUnknown-{prefix}",
            ip=f"10.71.3.{int(uuid4().hex[:2], 16) % 200 + 10}",
            status="ONLINE",
            agent_artifact_digest=None,
            extra={
                "agent_installed": True,
                "health": {"status": "HEALTHY", "reasons": []},
                "capacity": {"online_healthy_devices": 4},
            },
            mount_status={},
        )
        # 4) OFFLINE + installed + drift -> sync=drift 但 NOT upgrade_pending（离线不进 trackable）
        h_offline_drift = _make_host(
            db_session,
            host_id=f"{prefix}-04",
            name=f"NodeOfflineDrift-{prefix}",
            ip=f"10.71.4.{int(uuid4().hex[:2], 16) % 200 + 10}",
            status="OFFLINE",
            agent_artifact_digest=DRIFT_DIGEST,
            extra={"agent_installed": True},
        )
        # 5) 新鲜 DEGRADED + usb_tree_empty 但已 emptied_at（设备面已处置 → 豁免 USB 异常！）
        h_degraded_emptied = _make_host(
            db_session,
            host_id=f"{prefix}-05",
            name=f"NodeDegEmptied-{prefix}",
            ip=f"10.71.5.{int(uuid4().hex[:2], 16) % 200 + 10}",
            status="DEGRADED",
            last_heartbeat=now,
            emptied_at=now - timedelta(hours=1),
            extra={
                "agent_installed": True,
                "health": {"status": "DEGRADED", "reasons": ["usb_tree_empty"]},
            },
        )
        # 6) 已退役主机 + USB 故障（默认不计入 facets；include_retired=true 时计入总数但退役机 usb_anomaly 仍为 0）
        _make_host(
            db_session,
            host_id=f"{prefix}-06",
            name=f"NodeRetired-{prefix}",
            ip=f"10.71.6.{int(uuid4().hex[:2], 16) % 200 + 10}",
            status="OFFLINE",
            retired_at=now - timedelta(days=2),
            extra={"health": {"status": "UNSCHEDULABLE", "reasons": ["usb_host_controller_dead"]}},
        )
        db_session.commit()

        dml, commits, cleanup = _attach_readonly_probe(db_session)
        try:
            resp = client.get("/api/v1/hosts/facets", headers=auth_headers)
            assert resp.status_code == 200
            cur = resp.json()["data"]

            assert set(cur.keys()) == {
                "all",
                "online",
                "degraded",
                "offline",
                "usb_anomaly",
                "upgrade_pending",
                "health_anomaly",
                "mount_anomaly",
                "aligned_agent_count",
                "trackable_agent_count",
            }
            assert cur["all"] - base["all"] == 5
            assert cur["online"] - base["online"] == 3
            assert cur["degraded"] - base["degraded"] == 1
            assert cur["offline"] - base["offline"] == 1
            # h_degraded_emptied 豁免 usb_tree_empty，h_retired 默认隐藏 -> 仅 h_drift 计入 usb_anomaly
            assert cur["usb_anomaly"] - base["usb_anomaly"] == 1
            # h_offline_drift 不计入 upgrade_pending，仅 h_drift
            assert cur["upgrade_pending"] - base["upgrade_pending"] == 1
            # h_drift (UNSCHEDULABLE) + h_degraded_emptied (DEGRADED)
            assert cur["health_anomaly"] - base["health_anomaly"] == 2
            # 空 mount_status 不计入，仅 h_drift
            assert cur["mount_anomaly"] - base["mount_anomaly"] == 1
            assert cur["aligned_agent_count"] - base["aligned_agent_count"] == 1
            # h_matched + h_drift + h_unknown（unknown 保留在分母中）
            assert cur["trackable_agent_count"] - base["trackable_agent_count"] == 3

            # include_retired=true 包含退役主机，但退役主机 usb_anomaly 仍为 0
            cur_ret = client.get(
                "/api/v1/hosts/facets",
                params={"include_retired": "true"},
                headers=auth_headers,
            ).json()["data"]
            assert cur_ret["all"] == cur["all"] + 1
            assert cur_ret["usb_anomaly"] == cur["usb_anomaly"]

            # 快捷筛选 sync_status=upgrade_pending 只返回 h_drift（不含离线 drift 的 h_offline_drift）
            r_up = client.get(
                "/api/v1/hosts",
                params={"q": prefix, "sync_status": "upgrade_pending", "skip": 0, "limit": 50},
                headers=auth_headers,
            )
            assert r_up.status_code == 200
            assert r_up.json()["total"] == 1
            assert [h["id"] for h in r_up.json()["items"]] == [h_drift.id]

            # 对比 raw sync_status=drift 会同时命中在线与离线 drift
            r_raw_drift = client.get(
                "/api/v1/hosts",
                params={"q": prefix, "sync_status": "drift", "skip": 0, "limit": 50},
                headers=auth_headers,
            )
            assert {h["id"] for h in r_raw_drift.json()["items"]} == {h_drift.id, h_offline_drift.id}

            # usb_anomaly=true 与 mount_anomaly=true
            r_usb = client.get(
                "/api/v1/hosts",
                params={"q": prefix, "usb_anomaly": "true", "skip": 0, "limit": 50},
                headers=auth_headers,
            )
            assert [h["id"] for h in r_usb.json()["items"]] == [h_drift.id]

            r_mnt = client.get(
                "/api/v1/hosts",
                params={"q": prefix, "mount_anomaly": "true", "skip": 0, "limit": 50},
                headers=auth_headers,
            )
            assert [h["id"] for h in r_mnt.json()["items"]] == [h_drift.id]

            # 防止 unused 警告并确认 h_matched / h_unknown / h_degraded_emptied 可检索
            r_tok = client.get(
                "/api/v1/hosts",
                params={"q": f"{prefix} rev111", "skip": 0, "limit": 50},
                headers=auth_headers,
            )
            assert [h["id"] for h in r_tok.json()["items"]] == [h_matched.id]
            assert h_unknown.id and h_degraded_emptied.id

            assert dml == []
            assert commits == []
        finally:
            cleanup()


class TestHostSortingPaginationAndSelectionPreview:
    """GET /api/v1/hosts 全局 USB 优先级排序/显式排序/分页 + POST /api/v1/hosts/selection-preview。"""

    def test_global_usb_priority_before_pagination_and_explicit_sort(
        self, client, db_session, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(hosts_route, "_list_desired_artifact_digest", lambda: DESIRED_DIGEST)
        prefix = f"b22s-{uuid4().hex[:6]}"

        # 按 host_id 升序为 a < b < c < d，但 USB 优先级故意倒置：d(P3) > c(P2) > b(P1) > a(P0)
        h_a = _make_host(
            db_session,
            host_id=f"{prefix}-a",
            name=f"Zeta-{prefix}",
            ip=f"10.72.1.{int(uuid4().hex[:2], 16) % 200 + 10}",
            extra={"health": {"status": "HEALTHY", "reasons": []}, "capacity": {"online_healthy_devices": 12}},
        )
        h_b = _make_host(
            db_session,
            host_id=f"{prefix}-b",
            name=f"Beta-{prefix}",
            ip=f"10.72.2.{int(uuid4().hex[:2], 16) % 200 + 10}",
            extra={"health": {"status": "DEGRADED", "reasons": ["adb_interfaces_missing"]}, "capacity": {"online_healthy_devices": 5}},
        )
        h_c = _make_host(
            db_session,
            host_id=f"{prefix}-c",
            name=f"Gamma-{prefix}",
            ip=f"10.72.3.{int(uuid4().hex[:2], 16) % 200 + 10}",
            extra={"health": {"status": "DEGRADED", "reasons": ["usb_link_degraded"]}, "capacity": {"online_healthy_devices": 2}},
        )
        h_d = _make_host(
            db_session,
            host_id=f"{prefix}-d",
            name=f"Alpha-{prefix}",
            ip=f"10.72.4.{int(uuid4().hex[:2], 16) % 200 + 10}",
            extra={"health": {"status": "UNSCHEDULABLE", "reasons": ["usb_host_controller_dead"]}, "capacity": {"online_healthy_devices": 0}},
        )
        db_session.commit()

        # sort_by=priority 分页：第 1 页（limit=2）必须先排全局最高优先级 [d(P3), c(P2)]
        p1 = client.get(
            "/api/v1/hosts",
            params={"q": prefix, "sort_by": "priority", "skip": 0, "limit": 2},
            headers=auth_headers,
        )
        assert p1.status_code == 200
        assert p1.json()["total"] == 4
        assert [h["id"] for h in p1.json()["items"]] == [h_d.id, h_c.id]

        p2 = client.get(
            "/api/v1/hosts",
            params={"q": prefix, "sort_by": "priority", "skip": 2, "limit": 2},
            headers=auth_headers,
        )
        assert [h["id"] for h in p2.json()["items"]] == [h_b.id, h_a.id]

        # 显式列排序：devices desc
        r_dev = client.get(
            "/api/v1/hosts",
            params={"q": prefix, "sort_by": "devices", "sort_dir": "desc", "skip": 0, "limit": 10},
            headers=auth_headers,
        )
        assert [h["id"] for h in r_dev.json()["items"]] == [h_a.id, h_b.id, h_c.id, h_d.id]

        # 中文健康原因分词 AND 检索（q="主控失联" + prefix）
        r_cn = client.get(
            "/api/v1/hosts",
            params={"q": f"{prefix} 主控失联", "skip": 0, "limit": 10},
            headers=auth_headers,
        )
        assert [h["id"] for h in r_cn.json()["items"]] == [h_d.id]

        # 非法 sort_by / health_status / sync_status 返回 422
        assert client.get("/api/v1/hosts", params={"sort_by": "bogus"}, headers=auth_headers).status_code == 422
        assert client.get("/api/v1/hosts", params={"health_status": "bogus"}, headers=auth_headers).status_code == 422
        assert client.get("/api/v1/hosts", params={"sync_status": "bogus"}, headers=auth_headers).status_code == 422

    def test_host_selection_preview_freezes_ids_and_action_counts(
        self, client, db_session, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(hosts_route, "_list_desired_artifact_digest", lambda: DESIRED_DIGEST)
        prefix = f"b22p-{uuid4().hex[:6]}"

        h_online = _make_host(
            db_session,
            host_id=f"{prefix}-1",
            name=f"PrevOn-{prefix}",
            ip=f"10.73.1.{int(uuid4().hex[:2], 16) % 200 + 10}",
            status="ONLINE",
            extra={"agent_installed": True},
        )
        h_reinstall = _make_host(
            db_session,
            host_id=f"{prefix}-2",
            name=f"PrevRe-{prefix}",
            ip=f"10.73.2.{int(uuid4().hex[:2], 16) % 200 + 10}",
            status="OFFLINE",
            extra={"agent_installed": True},
        )
        h_first = _make_host(
            db_session,
            host_id=f"{prefix}-3",
            name=f"PrevFirst-{prefix}",
            ip=f"10.73.3.{int(uuid4().hex[:2], 16) % 200 + 10}",
            status="OFFLINE",
            set_heartbeat_none=True,
            extra={},
        )
        db_session.commit()

        dml, commits, cleanup = _attach_readonly_probe(db_session)
        try:
            resp = client.post(
                "/api/v1/hosts/selection-preview",
                json={"q": prefix},
                headers=auth_headers,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["total_matched"] == 3
            assert data["host_ids"] == [h_online.id, h_reinstall.id, h_first.id]
            assert data["online_count"] == 1
            assert data["offline_count"] == 2
            assert data["degraded_count"] == 0
            assert data["retired_count"] == 0
            assert data["hot_update_count"] == 1
            assert data["reinstall_count"] == 1
            assert data["first_install_count"] == 1
            assert data["flash_prereqs_count"] == 2
            assert dml == []
            assert commits == []
        finally:
            cleanup()
