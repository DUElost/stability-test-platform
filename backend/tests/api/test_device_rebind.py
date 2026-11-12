"""#3892：设备改绑端点（admin + 审计；幂等）API 测试。

覆盖：
1. admin 门禁（403）；
2. 改绑成功（写 host_id、不改写 status、审计 fail-closed 留 from/to/reason）；
3. 前置：设备退役 / 目标主机退役 / 活跃 Job / ACTIVE 租约 → 409；
4. 设备或目标主机不存在 → 404；
5. 幂等（目标即当前归属：原样返回、不重复审计）；
6. 校验：host_id / reason 空白 → 422。
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

_NOW = datetime.now(timezone.utc)


def _host(db, host_id: str, *, retired: bool = False) -> Host:
    host = db.get(Host, host_id)
    if host is None:
        host = Host(
            id=host_id, hostname=host_id, status="ONLINE",
            last_heartbeat=_NOW, created_at=_NOW,
        )
        db.add(host)
    if retired:
        host.retired_at = _NOW
    db.commit()
    return host


def _device(db, serial: str, *, host_id: str = "rb-h1", retired: bool = False) -> Device:
    _host(db, host_id)
    device = Device(
        serial=serial, host_id=host_id, status="OFFLINE", tags=[],
        created_at=_NOW, last_seen=_NOW,
    )
    if retired:
        device.retired_at = _NOW
        device.retired_by = "admin"
        device.retire_reason = "初始退役"
    db.add(device)
    db.commit()
    return device


def _audit_rows(db, device_id: int) -> list[AuditLog]:
    db.expire_all()
    return list(db.execute(
        select(AuditLog).where(
            AuditLog.resource_type == "device",
            AuditLog.resource_id == str(device_id),
        )
    ).scalars())


def _active_job(db, device: Device) -> None:
    plan = Plan(name=f"plan-job-{device.serial}")
    db.add(plan)
    db.flush()
    run = PlanRun(plan_id=plan.id, status="SUCCESS", plan_snapshot={}, run_type="MANUAL")
    db.add(run)
    db.flush()
    db.add(JobInstance(
        plan_run_id=run.id, plan_id=plan.id, device_id=device.id,
        host_id=device.host_id, status="PENDING", pipeline_def={"lifecycle": {}},
    ))
    db.commit()


def _active_lease(db, device: Device) -> None:
    db.add(DeviceLease(
        device_id=device.id, host_id=device.host_id or "rb-h1",
        lease_type="JOB", status="ACTIVE", fencing_token="tok", lease_generation=1,
        agent_instance_id="agent-x", expires_at=_NOW + timedelta(hours=1),
    ))
    db.commit()


def _rebind(client, device_id, host_id, headers, reason="换线到目标主机"):
    return client.post(
        f"/api/v1/devices/{device_id}/rebind",
        json={"host_id": host_id, "reason": reason}, headers=headers,
    )


class TestRebindApi:
    def test_requires_admin(self, client, db_session, auth_headers):
        device = _device(db_session, "RB-NONADMIN")
        _host(db_session, "rb-h2")
        assert _rebind(client, device.id, "rb-h2", auth_headers).status_code == 403
        db_session.expire_all()
        assert db_session.get(Device, device.id).host_id == "rb-h1"

    def test_rebind_changes_host_keeps_status_and_audits(
        self, client, db_session, admin_headers,
    ):
        device = _device(db_session, "RB-OK")
        _host(db_session, "rb-h2")

        resp = _rebind(client, device.id, "rb-h2", admin_headers, reason="15.94 改绑")

        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["host_id"] == "rb-h2"
        # 改绑不改写 status（status 归心跳所有）
        assert body["status"] == "OFFLINE"
        rows = _audit_rows(db_session, device.id)
        assert [r.action for r in rows] == ["rebind_device"]
        details = rows[0].details
        assert details["serial"] == "RB-OK"
        assert details["from_host_id"] == "rb-h1"
        assert details["to_host_id"] == "rb-h2"
        assert details["reason"] == "15.94 改绑"
        db_session.expire_all()
        assert db_session.get(Device, device.id).host_id == "rb-h2"

    def test_device_not_found(self, client, db_session, admin_headers):
        _host(db_session, "rb-h2")
        resp = _rebind(client, 999999, "rb-h2", admin_headers)
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "DEVICE_NOT_FOUND"

    def test_target_host_not_found(self, client, db_session, admin_headers):
        device = _device(db_session, "RB-NOHOST")
        resp = _rebind(client, device.id, "rb-missing", admin_headers)
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "HOST_NOT_FOUND"
        db_session.expire_all()
        assert db_session.get(Device, device.id).host_id == "rb-h1"

    def test_retired_device_rejected(self, client, db_session, admin_headers):
        device = _device(db_session, "RB-RETIRED", retired=True)
        _host(db_session, "rb-h2")
        resp = _rebind(client, device.id, "rb-h2", admin_headers)
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "DEVICE_RETIRED"
        db_session.expire_all()
        assert db_session.get(Device, device.id).host_id == "rb-h1"

    def test_retired_target_host_rejected(self, client, db_session, admin_headers):
        device = _device(db_session, "RB-TO-RETIRED")
        _host(db_session, "rb-h2", retired=True)
        resp = _rebind(client, device.id, "rb-h2", admin_headers)
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "HOST_RETIRED"
        db_session.expire_all()
        assert db_session.get(Device, device.id).host_id == "rb-h1"

    def test_blocked_by_active_job(self, client, db_session, admin_headers):
        device = _device(db_session, "RB-JOB")
        _host(db_session, "rb-h2")
        _active_job(db_session, device)
        resp = _rebind(client, device.id, "rb-h2", admin_headers)
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "DEVICE_HAS_ACTIVE_WORK"
        db_session.expire_all()
        assert db_session.get(Device, device.id).host_id == "rb-h1"

    def test_blocked_by_active_lease(self, client, db_session, admin_headers):
        device = _device(db_session, "RB-LEASE")
        _host(db_session, "rb-h2")
        _active_lease(db_session, device)
        resp = _rebind(client, device.id, "rb-h2", admin_headers)
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "DEVICE_HAS_ACTIVE_WORK"
        db_session.expire_all()
        assert db_session.get(Device, device.id).host_id == "rb-h1"

    def test_idempotent_same_host_no_audit(self, client, db_session, admin_headers):
        device = _device(db_session, "RB-IDEM")
        first = _rebind(client, device.id, "rb-h1", admin_headers)
        second = _rebind(client, device.id, "rb-h1", admin_headers)
        assert first.status_code == second.status_code == 200
        assert second.json()["data"]["host_id"] == "rb-h1"
        assert _audit_rows(db_session, device.id) == []

    def test_blank_fields_rejected(self, client, db_session, admin_headers):
        device = _device(db_session, "RB-BLANK")
        _host(db_session, "rb-h2")
        assert _rebind(client, device.id, "  ", admin_headers).status_code == 422
        assert _rebind(client, device.id, "rb-h2", admin_headers, reason="  ").status_code == 422
        assert _rebind(client, device.id, "", admin_headers).status_code == 422