"""阶段 B2-1 验收测试：物理设备服务端分页、有界列表 DTO、三级 Facets 与跨页选择预览（#496 / #4117）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.orm import Session

from backend.models.host import Device, Host
from backend.models.project import TestProject
from backend.models.project_model import ProjectModel
from backend.services.device_inventory_query import DEVICE_BULK_WRITE_MAX_IDS


def _make_host(
    db: Session,
    *,
    host_id: str,
    name: str,
    ip: str,
    status: str = "ONLINE",
    last_heartbeat: datetime | None = None,
    retired_at: datetime | None = None,
) -> Host:
    now = datetime.now(timezone.utc)
    host = Host(
        id=host_id,
        hostname=name,
        name=name,
        ip=ip,
        ip_address=ip,
        status=status,
        last_heartbeat=last_heartbeat if last_heartbeat is not None else now,
        retired_at=retired_at,
    )
    db.add(host)
    db.flush()
    return host


def _make_device(
    db: Session,
    *,
    serial: str,
    host_id: str | None = None,
    model: str | None = "MLD_NX3",
    product: str | None = "MLD-NX3",
    build_display_id: str | None = "BUILD-1.0",
    status: str = "ONLINE",
    tags=None,
    extra=None,
    last_seen: datetime | None = None,
    retired_at: datetime | None = None,
) -> Device:
    now = datetime.now(timezone.utc)
    dev = Device(
        serial=serial,
        host_id=host_id,
        model=model,
        product=product,
        build_display_id=build_display_id,
        status=status,
        tags=[] if tags is None else tags,
        extra={} if extra is None else extra,
        last_seen=last_seen if last_seen is not None else now,
        retired_at=retired_at,
    )
    db.add(dev)
    db.flush()
    return dev


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


class TestDeviceListB21FiltersAndProjection:
    """GET /api/v1/devices：有界投影、q 转义/别名/标签、status/host_id/has_tags、排序白名单。"""

    def test_projection_list_omits_unbounded_extra_and_stays_under_100kib(
        self, client, db_session, auth_headers
    ):
        prefix = f"B21-SZ-{uuid4().hex[:6]}"
        host = _make_host(
            db_session,
            host_id=f"h-{prefix}",
            name=f"Node-{prefix}",
            ip=f"10.88.1.{int(uuid4().hex[:2], 16) % 200 + 10}",
        )
        # 写入超大 extra 载荷模拟历史脏数据，验证 projection=list 严格有界
        huge_extra = {"blob": "X" * 4096, "nested": ["Y" * 512 for _ in range(4)]}
        for i in range(50):
            _make_device(
                db_session,
                serial=f"{prefix}-{i:03d}",
                host_id=host.id,
                model="MLD_NX3",
                product="MLD-NX3",
                build_display_id="BUILD-20261125",
                tags=["canary", f"slot-{i % 5}"],
                extra=huge_extra,
            )
        db_session.commit()

        # 旧默认形态仍返回 DeviceOut（含 extra）且不传分页返回裸数组
        legacy = client.get(f"/api/v1/devices?q={prefix}", headers=auth_headers)
        assert legacy.status_code == 200
        legacy_data = legacy.json()
        assert isinstance(legacy_data, list)
        assert len(legacy_data) == 50
        assert "extra" in legacy_data[0]

        # 新有界投影：projection=list&skip=0&limit=50 不含 extra、带 host_name、原始 UTF-8 ≤ 100 KiB
        resp = client.get(
            f"/api/v1/devices?q={prefix}&projection=list&skip=0&limit=50",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        raw_bytes = len(resp.content)
        assert raw_bytes <= 100 * 1024, f"50 items projection=list too large: {raw_bytes} bytes"
        payload = resp.json()
        assert payload["total"] == 50
        assert payload["skip"] == 0
        assert payload["limit"] == 50
        assert len(payload["items"]) == 50
        first = payload["items"][0]
        assert "extra" not in first
        assert first["host_name"] == host.name

    def test_q_literal_escape_product_alias_host_name_and_tag_without_duplicates(
        self, client, db_session, auth_headers
    ):
        prefix = f"B21-Q-{uuid4().hex[:6]}"
        host = _make_host(
            db_session,
            host_id=f"h-{prefix}",
            name=f"AlphaRack-{prefix}",
            ip=f"10.89.1.{int(uuid4().hex[:2], 16) % 200 + 10}",
        )
        # d1: serial 含字面 % 与 _，product 触发 NX3Carrier 别名，且有两个命中同子串的标签（验证不重复行）
        d1 = _make_device(
            db_session,
            serial=f"{prefix}_100%_A",
            host_id=host.id,
            model="MLD-NX3",
            product="sys_mssi_64_cn_armv82",
            build_display_id="VER-CARRIER-99",
            tags=[f"burnin-{prefix}-1", f"burnin-{prefix}-2"],
        )
        # d2: serial 无 %，product 为空（应命中「未上报」），model 为空（应命中「未知设备」）
        d2 = _make_device(
            db_session,
            serial=f"{prefix}-PLAIN-B",
            host_id=None,
            model=None,
            product=None,
            build_display_id="VER-OTHER",
            tags=[],
        )
        db_session.commit()

        # 1) % 与 _ 按字面匹配，不得当通配符命中 d2
        r_pct = client.get(
            "/api/v1/devices",
            params={"q": f"{prefix}_100%", "skip": 0, "limit": 50},
            headers=auth_headers,
        )
        assert r_pct.status_code == 200
        assert [item["id"] for item in r_pct.json()["items"]] == [d1.id]

        # 2) Product 展示别名（NX3Carrier）
        r_alias = client.get(
            "/api/v1/devices",
            params={"q": "nx3carrier", "host_id": host.id, "skip": 0, "limit": 50},
            headers=auth_headers,
        )
        assert [item["id"] for item in r_alias.json()["items"]] == [d1.id]

        # 3) 「未上报」命中空 product，且限定 host_id=unassigned
        r_unreported = client.get(
            "/api/v1/devices",
            params={"q": "未上报", "host_id": "unassigned", "skip": 0, "limit": 50},
            headers=auth_headers,
        )
        ids_unreported = [item["id"] for item in r_unreported.json()["items"]]
        assert d2.id in ids_unreported
        assert d1.id not in ids_unreported

        # 4) 主机展示名命中
        r_host = client.get(
            "/api/v1/devices",
            params={"q": f"alpharack-{prefix}", "skip": 0, "limit": 50},
            headers=auth_headers,
        )
        assert [item["id"] for item in r_host.json()["items"]] == [d1.id]

        # 5) 标签搜索匹配多个标签时，设备只返回一行且 total 严格为 1
        r_tag = client.get(
            "/api/v1/devices",
            params={"q": f"burnin-{prefix}", "skip": 0, "limit": 50},
            headers=auth_headers,
        )
        assert r_tag.json()["total"] == 1
        assert [item["id"] for item in r_tag.json()["items"]] == [d1.id]

    def test_status_aliases_need_attention_has_tags_and_sort_whitelist(
        self, client, db_session, auth_headers
    ):
        prefix = f"B21-ST-{uuid4().hex[:6]}"
        host_a = _make_host(
            db_session,
            host_id=f"ha-{prefix}",
            name=f"HostA-{prefix}",
            ip=f"10.90.1.{int(uuid4().hex[:2], 16) % 200 + 10}",
        )
        host_b = _make_host(
            db_session,
            host_id=f"hb-{prefix}",
            name=f"HostB-{prefix}",
            ip=f"10.90.2.{int(uuid4().hex[:2], 16) % 200 + 10}",
        )
        d_online = _make_device(
            db_session, serial=f"{prefix}-02", host_id=host_b.id, status="ONLINE", tags=["vip"]
        )
        d_busy = _make_device(
            db_session, serial=f"{prefix}-01", host_id=host_a.id, status="BUSY", tags=[]
        )
        d_offline = _make_device(
            db_session, serial=f"{prefix}-04", host_id=host_a.id, status="OFFLINE", tags={"legacy": True}
        )
        d_error = _make_device(
            db_session, serial=f"{prefix}-03", host_id=None, status="ERROR", tags=[]
        )
        db_session.commit()

        # status=need_attention == OFFLINE OR ERROR
        r_att = client.get(
            "/api/v1/devices",
            params={"q": prefix, "status": "need_attention", "skip": 0, "limit": 50},
            headers=auth_headers,
        )
        assert {item["id"] for item in r_att.json()["items"]} == {d_offline.id, d_error.id}

        # lowercase alias testing -> BUSY, idle -> ONLINE
        r_testing = client.get(
            "/api/v1/devices",
            params={"q": prefix, "status": "testing", "skip": 0, "limit": 50},
            headers=auth_headers,
        )
        assert [item["id"] for item in r_testing.json()["items"]] == [d_busy.id]

        # has_tags=true 仅命中非空数组；has_tags=false 命中空数组及历史非数组 dict
        r_tagged = client.get(
            "/api/v1/devices",
            params={"q": prefix, "has_tags": "true", "skip": 0, "limit": 50},
            headers=auth_headers,
        )
        assert [item["id"] for item in r_tagged.json()["items"]] == [d_online.id]

        r_untagged = client.get(
            "/api/v1/devices",
            params={"q": prefix, "has_tags": "false", "skip": 0, "limit": 50},
            headers=auth_headers,
        )
        assert {item["id"] for item in r_untagged.json()["items"]} == {d_busy.id, d_offline.id, d_error.id}

        # sort_by=serial&sort_dir=desc 稳定排序
        r_sort_serial = client.get(
            "/api/v1/devices",
            params={"q": prefix, "sort_by": "serial", "sort_dir": "desc", "skip": 0, "limit": 50},
            headers=auth_headers,
        )
        assert [item["serial"] for item in r_sort_serial.json()["items"]] == [
            f"{prefix}-04",
            f"{prefix}-03",
            f"{prefix}-02",
            f"{prefix}-01",
        ]

        # sort_by=host_id&sort_dir=desc 时无主机（NULL）固定 NULLS LAST
        r_sort_host = client.get(
            "/api/v1/devices",
            params={"q": prefix, "sort_by": "host_id", "sort_dir": "desc", "skip": 0, "limit": 50},
            headers=auth_headers,
        )
        assert r_sort_host.json()["items"][-1]["id"] == d_error.id

        # 非法 sort_by（如心跳遥测列 last_seen / battery_level）一律 422
        for bad in ("last_seen", "battery_level", "status", "host_name"):
            r_bad = client.get(
                "/api/v1/devices",
                params={"sort_by": bad},
                headers=auth_headers,
            )
            assert r_bad.status_code == 422


class TestDeviceFacetsThreeTierScopes:
    """GET /api/v1/devices/facets：三级作用域（B / H / D）、无 options.tags、纯只读。"""

    def test_three_tier_scopes_and_readonly_transaction(
        self, client, db_session, auth_headers
    ):
        prefix = f"B21-FC-{uuid4().hex[:6]}"
        proj = TestProject(
            project_key=f"proj-{prefix}",
            display_name=f"Proj {prefix}",
            source="USER",
            status="ACTIVE",
        )
        db_session.add(proj)
        db_session.flush()
        db_session.add_all([
            ProjectModel(project_id=proj.id, match_value=f"M1-{prefix}", is_active=True),
            ProjectModel(project_id=proj.id, match_value=f"M2-{prefix}", is_active=True),
        ])
        host_a = _make_host(
            db_session,
            host_id=f"h1-{prefix}",
            name=f"HostOne-{prefix}",
            ip=f"10.91.1.{int(uuid4().hex[:2], 16) % 200 + 10}",
        )
        host_b = _make_host(
            db_session,
            host_id=f"h2-{prefix}",
            name=f"HostTwo-{prefix}",
            ip=f"10.91.2.{int(uuid4().hex[:2], 16) % 200 + 10}",
        )
        _make_device(
            db_session,
            serial=f"{prefix}-1",
            host_id=host_a.id,
            model=f"M1-{prefix}",
            product="P-ONE",
            build_display_id="V-100",
            status="ONLINE",
            tags=["t1"],
        )
        _make_device(
            db_session,
            serial=f"{prefix}-2",
            host_id=host_b.id,
            model=f"M2-{prefix}",
            product="P-TWO",
            build_display_id="V-200",
            status="ONLINE",
            tags=[],
        )
        _make_device(
            db_session,
            serial=f"{prefix}-3",
            host_id=None,
            model=f"M2-{prefix}",
            product="P-ERR",
            build_display_id="V-ERR",
            status="ERROR",
            tags=["t2"],
        )
        db_session.commit()

        dml, commits, cleanup = _attach_readonly_probe(db_session)
        try:
            # 选中 status=ONLINE、host_id=host_a、model=M1、q=nomatch
            resp = client.get(
                "/api/v1/devices/facets",
                params={
                    "project_key": proj.project_key,
                    "status": "ONLINE",
                    "host_id": host_a.id,
                    "model": f"M1-{prefix}",
                    "q": "definitely-no-match",
                },
                headers=auth_headers,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]

            # 1) Scope B：status_counts 只受 project_key / stale / retired 影响（共 3 台）
            assert data["status_counts"] == {
                "all": 3,
                "ONLINE": 2,
                "BUSY": 0,
                "OFFLINE": 0,
                "ERROR": 1,
                "need_attention": 1,
                "tagged": 2,
            }
            # 2) Scope H：options.hosts 受 status=ONLINE 影响，但不被已选 host_id=host_a 锁死
            assert {opt["value"] for opt in data["options"]["hosts"]} == {host_a.id, host_b.id}
            # 3) Scope D：受有效 host_id=host_a 影响，不受 q 或同级 model 选择锁死
            assert data["options"]["models"] == [f"M1-{prefix}"]
            assert data["options"]["products"] == ["P-ONE"]
            assert data["options"]["versions"] == ["V-100"]
            # 首版不得输出未消费的 options.tags
            assert "tags" not in data["options"]

            # 4) 当 host_id 不在 Scope H 候选中（失效主机），Scope D 回退为不限主机
            resp_stale_host = client.get(
                "/api/v1/devices/facets",
                params={
                    "project_key": proj.project_key,
                    "status": "ONLINE",
                    "host_id": "nonexistent-host",
                },
                headers=auth_headers,
            )
            assert resp_stale_host.status_code == 200
            d_fallback = resp_stale_host.json()["data"]
            assert set(d_fallback["options"]["models"]) == {f"M1-{prefix}", f"M2-{prefix}"}
            assert set(d_fallback["options"]["products"]) == {"P-ONE", "P-TWO"}

            assert dml == []
            assert commits == []
        finally:
            cleanup()


class TestCrossPageSelectionPreviewAndBulkLimit:
    """POST /api/v1/devices/selection-preview 与批量写上限守卫（422 BULK_LIMIT_EXCEEDED）。"""

    def test_selection_preview_freezes_ids_and_impact_summary(
        self, client, db_session, auth_headers
    ):
        prefix = f"B21-PV-{uuid4().hex[:6]}"
        now = datetime.now(timezone.utc)
        host = _make_host(
            db_session,
            host_id=f"hp-{prefix}",
            name=f"PrevHost-{prefix}",
            ip=f"10.92.1.{int(uuid4().hex[:2], 16) % 200 + 10}",
        )
        d1 = _make_device(
            db_session,
            serial=f"{prefix}-A",
            host_id=host.id,
            model="MODEL-Z",
            status="ONLINE",
            last_seen=now,
        )
        d2 = _make_device(
            db_session,
            serial=f"{prefix}-B",
            host_id=host.id,
            model=None,
            status="OFFLINE",
            last_seen=now - timedelta(days=10),
        )
        d3 = _make_device(
            db_session,
            serial=f"{prefix}-C",
            host_id=host.id,
            model="MODEL-A",
            status="OFFLINE",
            last_seen=now,
            retired_at=now - timedelta(days=1),
        )
        db_session.commit()

        dml, commits, cleanup = _attach_readonly_probe(db_session)
        try:
            resp = client.post(
                "/api/v1/devices/selection-preview",
                json={
                    "action": "assign_project",
                    "q": prefix,
                    "include_stale": True,
                    "include_retired": True,
                },
                headers=auth_headers,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["action"] == "assign_project"
            assert data["total_matched"] == 3
            assert data["max_allowed"] == DEVICE_BULK_WRITE_MAX_IDS
            assert data["exceeds_limit"] is False
            assert data["device_ids"] == [d1.id, d2.id, d3.id]
            assert data["affected_models"] == ["MODEL-A", "MODEL-Z"]
            assert data["devices_without_model_serials"] == [d2.serial]
            assert data["retired_count"] == 1
            assert data["active_count"] == 2
            assert data["stale_count"] == 1
            assert dml == []
            assert commits == []
        finally:
            cleanup()

    def test_selection_preview_exceeds_limit_returns_empty_ids(
        self, client, db_session, auth_headers, monkeypatch
    ):
        from backend.services import device_inventory_query as diq

        prefix = f"B21-LM-{uuid4().hex[:6]}"
        host = _make_host(
            db_session,
            host_id=f"hl-{prefix}",
            name=f"LimHost-{prefix}",
            ip=f"10.93.1.{int(uuid4().hex[:2], 16) % 200 + 10}",
        )
        for i in range(3):
            _make_device(db_session, serial=f"{prefix}-{i}", host_id=host.id)
        db_session.commit()

        monkeypatch.setitem(diq.BULK_ACTION_MAX_LIMITS, "retire", 2)

        resp_write = client.post(
            "/api/v1/devices/selection-preview",
            json={"action": "retire", "q": prefix},
            headers=auth_headers,
        )
        assert resp_write.status_code == 200
        w_data = resp_write.json()["data"]
        assert w_data["total_matched"] == 3
        assert w_data["max_allowed"] == 2
        assert w_data["exceeds_limit"] is True
        # 超限不得静默截断，返回空 ID 集合
        assert w_data["device_ids"] == []

        # 只读导出无写上限，返回完整匹配 ID
        resp_export = client.post(
            "/api/v1/devices/selection-preview",
            json={"action": "export", "q": prefix},
            headers=auth_headers,
        )
        assert resp_export.status_code == 200
        e_data = resp_export.json()["data"]
        assert e_data["max_allowed"] is None
        assert e_data["exceeds_limit"] is False
        assert len(e_data["device_ids"]) == 3

    def test_bulk_write_endpoints_reject_over_limit_with_422(
        self, client, db_session, admin_headers
    ):
        proj = TestProject(
            project_key=f"lim-{uuid4().hex[:6]}",
            display_name="Limit Project",
            source="USER",
            status="ACTIVE",
        )
        db_session.add(proj)
        db_session.commit()

        over_ids = list(range(1, DEVICE_BULK_WRITE_MAX_IDS + 2))

        r_proj = client.post(
            "/api/v1/devices/bulk-project",
            json={"project_key": proj.project_key, "device_ids": over_ids},
            headers=admin_headers,
        )
        assert r_proj.status_code == 422
        assert r_proj.json()["detail"]["code"] == "BULK_LIMIT_EXCEEDED"

        r_swipe = client.post(
            "/api/v1/devices/bulk-swipe-trail",
            json={"device_ids": over_ids, "enabled": True},
            headers=admin_headers,
        )
        assert r_swipe.status_code == 422
        assert r_swipe.json()["detail"]["code"] == "BULK_LIMIT_EXCEEDED"

        r_retire = client.post(
            "/api/v1/devices/retire",
            json={"device_ids": over_ids, "retire_reason": "test limit"},
            headers=admin_headers,
        )
        assert r_retire.status_code == 422
        assert r_retire.json()["detail"]["code"] == "BULK_LIMIT_EXCEEDED"
