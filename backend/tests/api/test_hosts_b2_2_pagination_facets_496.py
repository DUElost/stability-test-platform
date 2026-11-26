"""阶段 B2-2 验收测试：Agent host 增量筛选/全局排序、独立 10 键 Facets 与跨页选择预览（#496 / #4117）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.orm import Session

from backend.api.routes import hosts as hosts_route
from backend.models.host import Device, Host

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


class TestHostVersionLookupHoisting:
    """回归：多台主机查询不得按 N 台重复调用 get_agent_code_version()（#496）。"""

    def test_list_calls_version_once_and_facets_preview_call_zero_times(
        self, client, auth_headers, db_session, monkeypatch
    ):
        import backend.services.agent_version_info as avi_mod
        import backend.services.host_inventory_query as hiq_mod

        monkeypatch.setattr(hosts_route, "_list_desired_artifact_digest", lambda: DESIRED_DIGEST)
        prefix = f"b22v-{uuid4().hex[:6]}"
        for idx in range(5):
            _make_host(
                db_session,
                host_id=f"{prefix}-{idx}",
                name=f"Hoist-{prefix}-{idx}",
                ip=f"10.74.{idx}.{int(uuid4().hex[:2], 16) % 200 + 10}",
                status="ONLINE",
                agent_artifact_digest=DESIRED_DIGEST,
                extra={"agent_installed": True},
            )
        db_session.commit()

        calls = {"count": 0}

        def _counting_version() -> str:
            calls["count"] += 1
            return "4afb332bc"

        monkeypatch.setattr(hiq_mod, "get_agent_code_version", _counting_version)
        monkeypatch.setattr(avi_mod, "get_agent_code_version", _counting_version)

        resp_list = client.get(
            f"/api/v1/hosts?skip=0&limit=50&q={prefix}",
            headers=auth_headers,
        )
        assert resp_list.status_code == 200
        items = resp_list.json()["items"]
        assert len(items) == 5
        assert all(item["expected_code_revision"] == "4afb332bc" for item in items)
        assert calls["count"] == 1

        calls["count"] = 0
        resp_facets = client.get("/api/v1/hosts/facets", headers=auth_headers)
        assert resp_facets.status_code == 200
        assert calls["count"] == 0

        resp_preview = client.post(
            "/api/v1/hosts/selection-preview",
            json={"q": prefix},
            headers=auth_headers,
        )
        assert resp_preview.status_code == 200
        assert resp_preview.json()["data"]["total_matched"] == 5
        assert calls["count"] == 0


class TestUsbControllerHealthAndEmptyCabinetDistinction:
    """区分纯空置主机（零台账且无硬件证据）与真实主控/端口故障或账实失明主机。"""

    def test_zero_ledger_empty_tree_excluded_while_ledger_and_hardware_faults_kept(
        self, client, db_session, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(hosts_route, "_list_desired_artifact_digest", lambda: DESIRED_DIGEST)
        prefix = f"usbd-{uuid4().hex[:6]}"
        now = datetime.now(timezone.utc)
        base = client.get("/api/v1/hosts/facets", headers=auth_headers).json()["data"]

        # 1) 纯空置主机：usb_tree_empty + 台账 0 设备 + 无硬件异常 -> 不进 usb_anomaly（P0）
        h_zero_ledger = _make_host(
            db_session,
            host_id=f"{prefix}-empty0",
            name=f"ZeroLedger-{prefix}",
            ip=f"10.74.1.{int(uuid4().hex[:2], 16) % 200 + 10}",
            extra={
                "health": {"status": "DEGRADED", "reasons": ["usb_tree_empty"]},
                "capacity": {
                    "usb_device_count": 0,
                    "usb_controller_health": {
                        "unbound_xhci_controllers": 0,
                        "disabled_root_ports": 0,
                        "disabled_hub_ports": 0,
                        "over_current_ports": 0,
                        "stuck_default_ports": 0,
                    },
                },
            },
        )
        # 2) 台账有在册设备 + usb_tree_empty + 未置位 emptied_at -> 进 usb_anomaly（P2）
        h_ledger_blind = _make_host(
            db_session,
            host_id=f"{prefix}-blind",
            name=f"LedgerBlind-{prefix}",
            ip=f"10.74.2.{int(uuid4().hex[:2], 16) % 200 + 10}",
            extra={
                "health": {"status": "DEGRADED", "reasons": ["usb_tree_empty"]},
                "capacity": {"usb_device_count": 0},
            },
        )
        db_session.add(
            Device(
                serial=f"{prefix}-SER-01",
                host_id=h_ledger_blind.id,
                model="TEST",
                platform="mtk",
                status="OFFLINE",
            )
        )
        # 3) 即使台账 0 且已置位 emptied_at，但存在硬件硬证据（root hub disable=1）-> P3 绝不豁免
        h_hw_disabled = _make_host(
            db_session,
            host_id=f"{prefix}-hw73",
            name=f"HwDisabled-{prefix}",
            ip=f"10.74.3.{int(uuid4().hex[:2], 16) % 200 + 10}",
            emptied_at=now - timedelta(hours=1),
            extra={
                "health": {"status": "DEGRADED", "reasons": ["usb_tree_empty"]},
                "capacity": {
                    "usb_device_count": 0,
                    "usb_controller_health": {
                        "unbound_xhci_controllers": 0,
                        "disabled_root_ports": 16,
                        "disabled_hub_ports": 0,
                        "over_current_ports": 0,
                        "stuck_default_ports": 0,
                    },
                },
            },
        )
        # 4) 台账 0 + 无 reason，但单口卡死 state=default（.57 形态）-> P1
        h_stuck_port = _make_host(
            db_session,
            host_id=f"{prefix}-stuck57",
            name=f"StuckPort-{prefix}",
            ip=f"10.74.4.{int(uuid4().hex[:2], 16) % 200 + 10}",
            extra={
                "health": {"status": "DEGRADED", "reasons": ["usb_tree_empty"]},
                "capacity": {
                    "usb_device_count": 0,
                    "usb_controller_health": {
                        "unbound_xhci_controllers": 0,
                        "disabled_root_ports": 0,
                        "disabled_hub_ports": 0,
                        "over_current_ports": 0,
                        "stuck_default_ports": 1,
                    },
                },
            },
        )
        db_session.commit()

        cur = client.get("/api/v1/hosts/facets", headers=auth_headers).json()["data"]
        # 4 台新增里：仅 h_ledger_blind(P2) + h_hw_disabled(P3) + h_stuck_port(P1) = 3 台计入 usb_anomaly
        assert cur["usb_anomaly"] - base["usb_anomaly"] == 3

        r_usb = client.get(
            "/api/v1/hosts",
            params={"q": prefix, "usb_anomaly": "true", "sort_by": "priority", "skip": 0, "limit": 50},
            headers=auth_headers,
        )
        assert r_usb.status_code == 200
        items = r_usb.json()["items"]
        # 全局优先级置顶顺序：P3(hw73) > P2(blind) > P1(stuck57)；零台账纯空柜(empty0)被排除
        assert [h["id"] for h in items] == [h_hw_disabled.id, h_ledger_blind.id, h_stuck_port.id]
        by_id = {h["id"]: h for h in items}
        assert by_id[h_ledger_blind.id]["ledger_device_count"] == 1
        assert by_id[h_hw_disabled.id]["ledger_device_count"] == 0

        # 单机详情接口同样返回 ledger_device_count
        r_detail = client.get(f"/api/v1/hosts/{h_zero_ledger.id}", headers=auth_headers)
        assert r_detail.status_code == 200
        assert r_detail.json()["ledger_device_count"] == 0

    def test_empty_tree_72h_stale_ledger_excluded_and_uhubctl_powered_off_surfaced(
        self, client, db_session, auth_headers
    ):
        """纯空树按 72h 最近活跃窗口消噪；uhubctl 断电端口（P1）与硬件故障绝不豁免。"""
        from backend.models.host import Device

        prefix = f"usb72h-{uuid4().hex[:6]}"
        now = datetime.now(timezone.utc)
        base = client.get("/api/v1/hosts/facets", headers=auth_headers).json()["data"]

        # 1) 长期空置机：台账有设备但 last_seen 为 5 天前（>72h）且硬件全绿 -> 自动排除出 usb_anomaly（P0）
        h_stale = _make_host(
            db_session,
            host_id=f"{prefix}-stale",
            name=f"StaleEmpty-{prefix}",
            ip=f"10.75.1.{int(uuid4().hex[:2], 16) % 200 + 10}",
            extra={
                "health": {"status": "DEGRADED", "reasons": ["usb_tree_empty"]},
                "capacity": {
                    "usb_device_count": 0,
                    "usb_controller_health": {
                        "unbound_xhci_controllers": 0,
                        "disabled_root_ports": 0,
                        "disabled_hub_ports": 0,
                        "over_current_ports": 0,
                        "stuck_default_ports": 0,
                    },
                },
            },
        )
        db_session.add(
            Device(
                serial=f"{prefix}-STALE-01",
                host_id=h_stale.id,
                model="TEST",
                platform="mtk",
                status="OFFLINE",
                last_seen=now - timedelta(days=5),
            )
        )

        # 2) 近期掉光机：台账设备 6 小时前还在活跃（<=72h）-> 保留在 usb_anomaly（P2）
        h_recent = _make_host(
            db_session,
            host_id=f"{prefix}-recent",
            name=f"RecentEmpty-{prefix}",
            ip=f"10.75.2.{int(uuid4().hex[:2], 16) % 200 + 10}",
            extra={
                "health": {"status": "DEGRADED", "reasons": ["usb_tree_empty"]},
                "capacity": {"usb_device_count": 0},
            },
        )
        db_session.add(
            Device(
                serial=f"{prefix}-RECENT-01",
                host_id=h_recent.id,
                model="TEST",
                platform="mtk",
                status="OFFLINE",
                last_seen=now - timedelta(hours=6),
            )
        )

        # 3) VBUS 断电机：无硬件损坏，但 uhubctl 巡检到 2 个 0000 off 端口 -> 计入 usb_anomaly（P1）
        h_vbus_off = _make_host(
            db_session,
            host_id=f"{prefix}-vbus",
            name=f"VbusOff-{prefix}",
            ip=f"10.75.3.{int(uuid4().hex[:2], 16) % 200 + 10}",
            extra={
                "health": {"status": "HEALTHY", "reasons": []},
                "capacity": {"usb_device_count": 2},
                "health_probe": {
                    "verdict": "aligned",
                    "topology": "ok",
                    "uhubctl_status": "ok",
                    "powered_off_ports": ["1-5.1:2", "1-5.1:4"],
                },
            },
        )
        db_session.commit()

        cur = client.get("/api/v1/hosts/facets", headers=auth_headers).json()["data"]
        assert cur["usb_anomaly"] - base["usb_anomaly"] == 2

        r_usb = client.get(
            "/api/v1/hosts",
            params={"q": prefix, "usb_anomaly": "true", "sort_by": "priority", "skip": 0, "limit": 50},
            headers=auth_headers,
        )
        assert r_usb.status_code == 200
        items = r_usb.json()["items"]
        assert [h["id"] for h in items] == [h_recent.id, h_vbus_off.id]
        assert items[0]["ledger_latest_seen_at"] is not None
        assert items[1]["health_probe"]["powered_off_ports"] == ["1-5.1:2", "1-5.1:4"]

        # 按关键词搜索 VBUS / 具体端口可命中
        r_q = client.get(
            "/api/v1/hosts",
            params={"q": f"{prefix} 1-5.1:2", "skip": 0, "limit": 50},
            headers=auth_headers,
        )
        assert r_q.status_code == 200
        assert [h["id"] for h in r_q.json()["items"]] == [h_vbus_off.id]

        # 长期空置机单机详情带 ledger_latest_seen_at
        r_stale = client.get(f"/api/v1/hosts/{h_stale.id}", headers=auth_headers)
        assert r_stale.status_code == 200
        assert r_stale.json()["ledger_device_count"] == 1
        assert r_stale.json()["ledger_latest_seen_at"] is not None

