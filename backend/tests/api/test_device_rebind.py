"""#3892：设备改绑端点（admin + 审计；幂等；静默期前置）API 测试。

覆盖：
1. admin 门禁（403）；
2. 改绑成功（写 host_id、不改写 status、审计 fail-closed 留 from/to/reason）；
3. 前置：设备退役 / 目标主机退役 / 活跃 Job / ACTIVE 租约 → 409；
4. 设备或目标主机不存在 → 404；
5. 幂等（目标即当前归属：原样返回、不重复审计），且同归属带阻断项仍返回对应 409；
6. 校验：host_id / reason 空白 → 422；
7. B1：目标主机在检查后、写入前被并发退役 → 409 HOST_RETIRED，归属不变、零审计；
8. B3：审计写失败（audit_logs 缺失）→ 500，host_id 回滚、零审计；
9. B4：静默期矩阵（在线 / 刚关机 / 持续关机 + 旧主机心跳重放 / 边界与 naive 时间）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from starlette.testclient import TestClient

from backend.core.database import SessionLocal, get_db
from backend.core.job_timeout_config import HOST_HEARTBEAT_TIMEOUT_SECONDS
from backend.main import fastapi_app
from backend.models.audit import AuditLog
from backend.models.device_lease import DeviceLease
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services import device_rebind
from backend.services.errors import Conflict

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


def _device(
    db,
    serial: str,
    *,
    host_id: str = "rb-h1",
    retired: bool = False,
    adb_connected: bool = False,
    last_seen: datetime | None = None,
) -> Device:
    _host(db, host_id)
    device = Device(
        serial=serial, host_id=host_id, status="OFFLINE", tags=[],
        created_at=_NOW, last_seen=last_seen, adb_connected=adb_connected,
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

    @pytest.mark.parametrize(
        ("blocker", "expected_code"),
        [
            ("host_retired", "HOST_RETIRED"),
            ("active_job", "DEVICE_HAS_ACTIVE_WORK"),
            ("active_lease", "DEVICE_HAS_ACTIVE_WORK"),
        ],
    )
    def test_same_host_replay_with_blocker_returns_409(
        self, client, db_session, admin_headers, blocker, expected_code,
    ):
        device = _device(db_session, f"RB-SAME-{blocker}")
        _host(db_session, "rb-h2")
        assert _rebind(client, device.id, "rb-h2", admin_headers).status_code == 200
        db_session.expire_all()
        device = db_session.get(Device, device.id)

        if blocker == "host_retired":
            response = client.post(
                "/api/v1/hosts/rb-h2/retire",
                headers=admin_headers,
                json={"retire_reason": "同归属重放前置"},
            )
            assert response.status_code == 200, response.text
        elif blocker == "active_job":
            _active_job(db_session, device)
        else:
            _active_lease(db_session, device)

        replay = _rebind(client, device.id, "rb-h2", admin_headers)
        assert replay.status_code == 409, replay.text
        assert replay.json()["detail"]["code"] == expected_code
        assert [row.action for row in _audit_rows(db_session, device.id)] == ["rebind_device"]

    def test_target_host_retirement_between_check_and_write_rejected(
        self, client, db_session, admin_headers, monkeypatch,
    ):
        """B1：目标主机在 Host 检查与 _assert_idle 之后、写入之前被真实退役。

        第二 session 执行生产同款 retire_host 并提交；改绑必须在锁内复检时
        看到 retired_at，返回 409 且不写 host_id、不落审计。
        """
        from backend.services.host_retirement import retire_host

        device = _device(db_session, "RB-HOST-RACE")
        _host(db_session, "rb-h2")
        original = device_rebind._assert_idle

        def retire_before_write(session, device_id):
            original(session, device_id)
            with SessionLocal() as other:
                retire_host(
                    other, host_id="rb-h2", reason="并发退役",
                    actor_id=None, actor_username="review",
                )

        monkeypatch.setattr(device_rebind, "_assert_idle", retire_before_write)
        resp = _rebind(client, device.id, "rb-h2", admin_headers)
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["code"] == "HOST_RETIRED"
        db_session.expire_all()
        assert db_session.get(Device, device.id).host_id == "rb-h1"
        assert _audit_rows(db_session, device.id) == []

    def test_audit_write_failure_rolls_back_host_change(
        self, client, db_session, engine, admin_headers,
    ):
        """B3：审计真源写不进去（表缺失）→ 500，host_id 回滚、零审计。

        经生产同款 get_db 会话边界调用（临时移除测试 fixture 的 override），
        与复核探针 test_missing_audit_table_rolls_back 等价。
        """
        device = _device(db_session, "RB-AUDIT-FAIL")
        _host(db_session, "rb-h2")
        device_id = device.id
        db_session.rollback()

        fixture_override = fastapi_app.dependency_overrides.pop(get_db)
        try:
            with engine.begin() as connection:
                connection.execute(text(
                    "ALTER TABLE audit_logs RENAME TO rebind_r1_hidden_audit_logs"
                ))
            try:
                probe = TestClient(fastapi_app, raise_server_exceptions=False)
                response = _rebind(probe, device_id, "rb-h2", admin_headers)
            finally:
                with engine.begin() as connection:
                    connection.execute(text(
                        "ALTER TABLE rebind_r1_hidden_audit_logs RENAME TO audit_logs"
                    ))
        finally:
            fastapi_app.dependency_overrides[get_db] = fixture_override

        assert response.status_code == 500, response.text
        db_session.expire_all()
        assert db_session.get(Device, device_id).host_id == "rb-h1"
        assert _audit_rows(db_session, device_id) == []

    def test_online_device_rejected(self, client, db_session, admin_headers):
        """B4-a：adb_connected=True（新鲜 ADB 观测）→ 409 DEVICE_NOT_QUIET。"""
        device = _device(
            db_session, "RB-ONLINE", adb_connected=True, last_seen=_NOW,
        )
        _host(db_session, "rb-h2")
        resp = _rebind(client, device.id, "rb-h2", admin_headers)
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["code"] == "DEVICE_NOT_QUIET"
        db_session.expire_all()
        assert db_session.get(Device, device.id).host_id == "rb-h1"
        assert _audit_rows(db_session, device.id) == []

    def test_just_powered_off_device_rejected(self, client, db_session, admin_headers):
        """B4-b：ADB 已断开但 last_seen 仍在静默期内 → 409 DEVICE_NOT_QUIET。"""
        device = _device(
            db_session, "RB-RECENT-OFF",
            adb_connected=False, last_seen=_NOW - timedelta(seconds=60),
        )
        _host(db_session, "rb-h2")
        resp = _rebind(client, device.id, "rb-h2", admin_headers)
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["code"] == "DEVICE_NOT_QUIET"
        db_session.expire_all()
        assert db_session.get(Device, device.id).host_id == "rb-h1"
        assert _audit_rows(db_session, device.id) == []

    def test_silent_device_rebind_and_old_host_presence_heartbeat_keeps_host(
        self, client, db_session, admin_headers,
    ):
        """B4-c：持续关机（静默超窗）可改绑；旧主机仅槽位在位心跳不改回归属。"""
        serial = "RB-SILENT"
        device = _device(
            db_session, serial,
            adb_connected=False, last_seen=_NOW - timedelta(hours=1),
        )
        _host(db_session, "rb-h2")
        assert _rebind(client, device.id, "rb-h2", admin_headers).status_code == 200

        old_beat = {
            "host_id": "rb-h1",
            "status": "ONLINE",
            "devices": [],
            "capacity": {"usb_power_slots": {serial: ["1-1:1", "0e8d:2046"]}},
        }
        assert client.post("/api/v1/heartbeat", json=old_beat).status_code == 200

        db_session.expire_all()
        assert db_session.get(Device, device.id).host_id == "rb-h2"
        assert [row.action for row in _audit_rows(db_session, device.id)] == ["rebind_device"]


class TestQuietPeriod:
    def test_boundary_and_naive_last_seen(self):
        """B4-d：恰好等于 now-QUIET_PERIOD 不算静默；naive 按 UTC 解释。"""
        fixed = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)
        boundary = fixed - timedelta(seconds=HOST_HEARTBEAT_TIMEOUT_SECONDS)

        for last_seen in (boundary, boundary.replace(tzinfo=None)):
            device = Device(
                serial="RB-QUIET-UNIT", adb_connected=False, last_seen=last_seen,
            )
            with pytest.raises(Conflict) as exc:
                device_rebind._assert_quiet(device, now=fixed)
            assert exc.value.detail["code"] == "DEVICE_NOT_QUIET"

        older = boundary - timedelta(seconds=1)
        for last_seen in (older, older.replace(tzinfo=None)):
            device = Device(
                serial="RB-QUIET-UNIT", adb_connected=False, last_seen=last_seen,
            )
            device_rebind._assert_quiet(device, now=fixed)

        device_rebind._assert_quiet(
            Device(serial="RB-QUIET-UNIT", adb_connected=False, last_seen=None),
            now=fixed,
        )
        with pytest.raises(Conflict):
            device_rebind._assert_quiet(
                Device(serial="RB-QUIET-UNIT", adb_connected=True, last_seen=None),
                now=fixed,
            )
