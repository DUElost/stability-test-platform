"""#2962 B / ADR-0057 D2·D4：设备退役 / 解除退役 API 与八面读侧收口。

覆盖：
1. admin 门禁（403）；
2. retire 成功（写四列、不改写 status、审计 fail-closed 留痕）；
3. E2 前置：活跃 Job / ACTIVE 租约 → 409；
4. 幂等（重复 retire 不重复审计；重复 unretire 无操作）；
5. unretire 写回：清 retired_at、保留最近一次痕迹；
6. 批量：逐台结果与计数（成功 / 幂等 / 冲突 / 未找到）；
7. 读侧：列表默认隐藏退役与陈旧（include_retired / include_stale 开关）；
8. 写路径：退役设备的标签与项目归属改写 → 409。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from backend.models.audit import AuditLog
from backend.models.device_lease import DeviceLease
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.project import TestProject as _TestProjectModel

_NOW = datetime.now(timezone.utc)


def _host(db, host_id: str = "dr-h1") -> Host:
    host = Host(
        id=host_id, hostname=host_id, status="ONLINE",
        last_heartbeat=_NOW, created_at=_NOW,
    )
    db.add(host)
    db.commit()
    return host


def _ensure_host(db, host_id: str) -> None:
    if db.get(Host, host_id) is None:
        db.add(Host(
            id=host_id, hostname=host_id, status="ONLINE",
            last_heartbeat=_NOW, created_at=_NOW,
        ))
        db.commit()


def _device(
    db,
    serial: str,
    *,
    host_id: str | None = "dr-h1",
    status: str = "ONLINE",
    last_seen: datetime | None = None,
    retired: bool = False,
) -> Device:
    if host_id is not None:
        _ensure_host(db, host_id)
    device = Device(
        serial=serial, host_id=host_id, status=status, tags=[], created_at=_NOW,
        last_seen=last_seen if last_seen is not None else _NOW,
    )
    if retired:
        device.retired_at = _NOW
        device.retired_by = "admin"
        device.retire_reason = "初始退役"
    db.add(device)
    db.commit()
    return device


def _audit_actions(db, device_id: int) -> list[str]:
    db.expire_all()
    return [
        row.action
        for row in db.execute(
            select(AuditLog).where(
                AuditLog.resource_type == "device",
                AuditLog.resource_id == str(device_id),
            )
        ).scalars()
    ]


def _active_job(db, device: Device) -> JobInstance:
    plan = Plan(name=f"plan-job-{device.serial}")
    db.add(plan)
    db.flush()
    run = PlanRun(plan_id=plan.id, status="SUCCESS", plan_snapshot={}, run_type="MANUAL")
    db.add(run)
    db.flush()
    job = JobInstance(
        plan_run_id=run.id, plan_id=plan.id, device_id=device.id,
        host_id=device.host_id, status="PENDING", pipeline_def={"lifecycle": {}},
    )
    db.add(job)
    db.commit()
    return job


def _active_lease(db, device: Device) -> DeviceLease:
    lease = DeviceLease(
        device_id=device.id, host_id=device.host_id or "dr-h1",
        lease_type="JOB", status="ACTIVE", fencing_token="tok", lease_generation=1,
        agent_instance_id="agent-x", expires_at=_NOW + timedelta(hours=1),
    )
    db.add(lease)
    db.commit()
    return lease


class TestRetireApi:
    def test_requires_admin(self, client, db_session, auth_headers):
        device = _device(db_session, "DR-NONADMIN")
        assert client.post(
            f"/api/v1/devices/{device.id}/retire",
            json={"retire_reason": "x"}, headers=auth_headers,
        ).status_code == 403
        assert client.post(
            f"/api/v1/devices/{device.id}/unretire",
            json={"retire_reason": "x"}, headers=auth_headers,
        ).status_code == 403
        assert client.post(
            "/api/v1/devices/retire",
            json={"device_ids": [device.id], "retire_reason": "x"}, headers=auth_headers,
        ).status_code == 403

    def test_retire_sets_facts_keeps_status_and_audits(
        self, client, db_session, admin_headers,
    ):
        _host(db_session)
        device = _device(db_session, "DR-OK", status="ONLINE")

        resp = client.post(
            f"/api/v1/devices/{device.id}/retire",
            json={"retire_reason": "库存陈旧已报废"}, headers=admin_headers,
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["retired_at"] is not None
        assert body["retired_by"] == "admin"
        assert body["retire_reason"] == "库存陈旧已报废"
        # D1：退役不改写 status（status 归心跳所有）
        assert body["status"] == "ONLINE"
        assert "retire_device" in _audit_actions(db_session, device.id)

    def test_retire_blocked_by_active_job(self, client, db_session, admin_headers):
        _host(db_session)
        device = _device(db_session, "DR-JOB")
        _active_job(db_session, device)

        resp = client.post(
            f"/api/v1/devices/{device.id}/retire",
            json={"retire_reason": "x"}, headers=admin_headers,
        )

        assert resp.status_code == 409
        db_session.expire_all()
        assert db_session.get(Device, device.id).retired_at is None

    def test_retire_blocked_by_active_lease(self, client, db_session, admin_headers):
        _host(db_session)
        device = _device(db_session, "DR-LEASE")
        _active_lease(db_session, device)

        resp = client.post(
            f"/api/v1/devices/{device.id}/retire",
            json={"retire_reason": "x"}, headers=admin_headers,
        )

        assert resp.status_code == 409
        db_session.expire_all()
        assert db_session.get(Device, device.id).retired_at is None

    def test_retire_is_idempotent_and_does_not_duplicate_audit(
        self, client, db_session, admin_headers,
    ):
        _host(db_session)
        device = _device(db_session, "DR-IDEM")

        first = client.post(
            f"/api/v1/devices/{device.id}/retire",
            json={"retire_reason": "第一次"}, headers=admin_headers,
        )
        second = client.post(
            f"/api/v1/devices/{device.id}/retire",
            json={"retire_reason": "第二次"}, headers=admin_headers,
        )

        assert first.status_code == second.status_code == 200
        assert first.json()["retired_at"] == second.json()["retired_at"]
        assert second.json()["retire_reason"] == "第一次", "幂等不得重写 who/when/reason"
        assert _audit_actions(db_session, device.id).count("retire_device") == 1

    def test_unretire_clears_and_keeps_trace(self, client, db_session, admin_headers):
        _host(db_session)
        device = _device(db_session, "DR-UN", retired=True)

        resp = client.post(
            f"/api/v1/devices/{device.id}/unretire",
            json={"retire_reason": "修复回场"}, headers=admin_headers,
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["retired_at"] is None
        # 保留最近一次退役痕迹（审计另有 before/after 快照）
        assert body["retired_by"] == "admin"
        assert body["retire_reason"] == "初始退役"
        assert "unretire_device" in _audit_actions(db_session, device.id)

        again = client.post(
            f"/api/v1/devices/{device.id}/unretire",
            json={"retire_reason": "再试"}, headers=admin_headers,
        )
        assert again.status_code == 200
        assert _audit_actions(db_session, device.id).count("unretire_device") == 1

    def test_batch_reports_per_device_results(self, client, db_session, admin_headers):
        _host(db_session)
        ok = _device(db_session, "DR-B-OK")
        blocked = _device(db_session, "DR-B-BLOCK")
        _active_job(db_session, blocked)

        resp = client.post(
            "/api/v1/devices/retire",
            json={"device_ids": [ok.id, blocked.id, 999999], "retire_reason": "批量清账"},
            headers=admin_headers,
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        by_id = {item["device_id"]: item for item in data["results"]}
        assert by_id[ok.id]["status"] == "retired"
        assert by_id[blocked.id]["status"] == "conflict"
        assert by_id[999999]["status"] == "not_found"
        assert data["retired"] == 1 and data["conflict"] == 1 and data["not_found"] == 1

        db_session.expire_all()
        assert db_session.get(Device, ok.id).retired_at is not None
        assert db_session.get(Device, blocked.id).retired_at is None


class TestDeviceLifecycleReadFilters:
    def test_list_hides_retired_and_stale_by_default(
        self, client, db_session, admin_headers,
    ):
        _host(db_session)
        normal = _device(db_session, "DR-L-NORMAL")
        retired = _device(db_session, "DR-L-RETIRED", retired=True)
        stale = _device(
            db_session, "DR-L-STALE", status="OFFLINE",
            last_seen=_NOW - timedelta(days=10),
        )

        default = client.get("/api/v1/devices", headers=admin_headers).json()
        default_ids = {d["id"] for d in default}
        assert default_ids == {normal.id}

        with_retired = client.get("/api/v1/devices", params={"include_retired": "true"}, headers=admin_headers).json()
        assert {d["id"] for d in with_retired} == {normal.id, retired.id}

        with_stale = client.get("/api/v1/devices", params={"include_stale": "true"}, headers=admin_headers).json()
        assert {d["id"] for d in with_stale} == {normal.id, stale.id}

        both = client.get(
            "/api/v1/devices",
            params={"include_retired": "true", "include_stale": "true"},
            headers=admin_headers,
        ).json()
        assert {d["id"] for d in both} == {normal.id, retired.id, stale.id}

    def test_device_out_exposes_staleness_and_suggestion(
        self, client, db_session, admin_headers,
    ):
        _host(db_session)
        _device(db_session, "DR-S-FRESH", status="OFFLINE",
                last_seen=_NOW - timedelta(hours=2))
        _device(db_session, "DR-S-STALE", status="OFFLINE",
                last_seen=_NOW - timedelta(days=10))
        _device(db_session, "DR-S-OLD", status="OFFLINE",
                last_seen=_NOW - timedelta(days=31))

        resp = client.get(
            "/api/v1/devices", params={"include_stale": "true", "status": "OFFLINE"},
            headers=admin_headers,
        ).json()
        by_serial = {d["serial"]: d for d in resp}
        assert by_serial["DR-S-FRESH"]["is_stale"] is False
        assert by_serial["DR-S-STALE"]["is_stale"] is True
        assert by_serial["DR-S-STALE"]["retire_suggested"] is False
        assert by_serial["DR-S-OLD"]["is_stale"] is True
        assert by_serial["DR-S-OLD"]["retire_suggested"] is True


class TestRetiredDeviceWritePaths:
    def test_tags_write_rejected(self, client, db_session, admin_headers):
        _host(db_session)
        device = _device(db_session, "DR-W-TAGS", retired=True)

        resp = client.put(
            f"/api/v1/devices/{device.id}/tags", json=["keep"], headers=admin_headers,
        )

        assert resp.status_code == 409
        db_session.expire_all()
        assert db_session.get(Device, device.id).tags == []

    def test_bulk_project_rejected(self, client, db_session, admin_headers):
        _host(db_session)
        device = _device(db_session, "DR-W-PROJ", retired=True)
        project = _TestProjectModel(
            project_key="dr-proj", display_name="DR 项目", status="ACTIVE", source="USER",
        )
        db_session.add(project)
        db_session.commit()

        resp = client.post(
            "/api/v1/devices/bulk-project",
            json={"project_key": "dr-proj", "device_ids": [device.id]},
            headers=admin_headers,
        )

        assert resp.status_code == 409
