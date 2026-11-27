"""#3904：/devices 暴露 ACTIVE 租约 type/reason，供首屏区分测试中 / 归还欠账 / 维护占用。

不改 Device.status；列表与详情同源投影；分页查询 SQL 次数不随设备数线性增长。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.orm import Session

from backend.models.device_lease import DeviceLease
from backend.models.enums import LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.services.device_return import DEBT_LEASE_HOLDER, DEBT_LEASE_REASON


class _SqlCountProbe:
    def __init__(self, engine):
        self._engine = engine
        self.statements: list[str] = []

    def _before_cursor_execute(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append(statement.lstrip())

    def __enter__(self):
        event.listen(self._engine, "before_cursor_execute", self._before_cursor_execute)
        return self

    def __exit__(self, exc_type, exc, tb):
        event.remove(self._engine, "before_cursor_execute", self._before_cursor_execute)


def _seed_host_device(
    db: Session,
    *,
    suffix: str,
    serial: str,
    status: str,
    return_state=None,
) -> Device:
    now = datetime.now(timezone.utc)
    host_id = f"h3904-{suffix}"
    db.add(
        Host(
            id=host_id,
            hostname=host_id,
            name=host_id,
            ip=f"10.90.{int(suffix[:2], 16)}.{int(suffix[2:4], 16)}",
            status="ONLINE",
            last_heartbeat=now,
            created_at=now,
        )
    )
    device = Device(
        serial=serial,
        host_id=host_id,
        model="MLD_NX3",
        status=status,
        tags=[],
        last_seen=now,
        return_state=return_state,
        created_at=now,
        lease_generation=1,
    )
    db.add(device)
    db.flush()
    return device


def _add_active_lease(
    db: Session,
    device: Device,
    *,
    lease_type: str,
    reason: str | None,
    job_id: int | None = None,
) -> DeviceLease:
    now = datetime.now(timezone.utc)
    lease = DeviceLease(
        device_id=device.id,
        job_id=job_id,
        host_id=device.host_id,
        lease_type=lease_type,
        status=LeaseStatus.ACTIVE.value,
        fencing_token=f"{device.id}:1",
        lease_generation=1,
        agent_instance_id="platform" if reason == DEBT_LEASE_REASON else f"agent-{device.id}",
        holder=DEBT_LEASE_HOLDER if reason == DEBT_LEASE_REASON else None,
        reason=reason,
        acquired_at=now,
        renewed_at=now,
        expires_at=datetime(9999, 1, 1, tzinfo=timezone.utc)
        if reason == DEBT_LEASE_REASON
        else now + timedelta(hours=1),
    )
    db.add(lease)
    db.flush()
    return lease


def test_list_and_detail_expose_maintenance_debt_lease_fields(client, db_session, auth_headers):
    """① 0 运行中 Job + ACTIVE MAINTENANCE 归还欠账：list/full/detail 均暴露 BUSY + 租约字段。"""
    suffix = uuid4().hex[:8]
    device = _seed_host_device(
        db_session,
        suffix=suffix,
        serial=f"DEBT-{suffix}",
        status="BUSY",
        return_state={"power-cycle": {"state": "debt", "source": "job"}},
    )
    _add_active_lease(
        db_session,
        device,
        lease_type=LeaseType.MAINTENANCE.value,
        reason=DEBT_LEASE_REASON,
    )
    db_session.commit()

    list_resp = client.get(
        f"/api/v1/devices?q=DEBT-{suffix}&projection=list&skip=0&limit=50",
        headers=auth_headers,
    )
    assert list_resp.status_code == 200
    list_item = next(i for i in list_resp.json()["items"] if i["id"] == device.id)
    assert list_item["status"] == "BUSY"
    assert list_item["active_lease_type"] == "MAINTENANCE"
    assert list_item["active_lease_reason"] == DEBT_LEASE_REASON

    full_resp = client.get(
        f"/api/v1/devices?q=DEBT-{suffix}&projection=full&skip=0&limit=50",
        headers=auth_headers,
    )
    assert full_resp.status_code == 200
    full_item = next(i for i in full_resp.json()["items"] if i["id"] == device.id)
    assert full_item["status"] == "BUSY"
    assert full_item["active_lease_type"] == "MAINTENANCE"
    assert full_item["active_lease_reason"] == DEBT_LEASE_REASON

    detail = client.get(f"/api/v1/devices/{device.id}", headers=auth_headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "BUSY"
    assert body["active_lease_type"] == "MAINTENANCE"
    assert body["active_lease_reason"] == DEBT_LEASE_REASON


def test_list_exposes_active_job_lease_type(client, db_session, auth_headers):
    """② ACTIVE JOB 租约设备返回 active_lease_type=JOB。"""
    suffix = uuid4().hex[:8]
    device = _seed_host_device(
        db_session,
        suffix=suffix,
        serial=f"JOB-{suffix}",
        status="BUSY",
    )
    _add_active_lease(
        db_session,
        device,
        lease_type=LeaseType.JOB.value,
        reason="plan-run",
    )
    db_session.commit()

    resp = client.get(
        f"/api/v1/devices?q=JOB-{suffix}&projection=list&skip=0&limit=50",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    item = next(i for i in resp.json()["items"] if i["id"] == device.id)
    assert item["status"] == "BUSY"
    assert item["active_lease_type"] == "JOB"
    assert item["active_lease_reason"] == "plan-run"


def test_list_devices_lease_projection_sql_count_constant(client, db_session, auth_headers):
    """③ 多台设备分页查询：SQL 次数不随设备数线性增长（零 N+1）。"""
    now = datetime.now(timezone.utc)
    suffix = uuid4().hex[:8]
    engine = db_session.get_bind()

    def _add_busy_with_lease(count: int, tag: str) -> None:
        for i in range(count):
            host_id = f"n1-3904-{tag}-{i}-{suffix}"
            db_session.add(
                Host(
                    id=host_id,
                    hostname=host_id,
                    name=host_id,
                    ip=f"10.91.{1 if tag == 'a' else 2}.{i + 1}",
                    status="ONLINE",
                    last_heartbeat=now,
                    created_at=now,
                )
            )
            device = Device(
                serial=f"N13904-{tag}-{i}-{suffix}",
                host_id=host_id,
                status="BUSY",
                tags=[],
                last_seen=now,
                created_at=now,
                lease_generation=1,
            )
            db_session.add(device)
            db_session.flush()
            db_session.add(
                DeviceLease(
                    device_id=device.id,
                    job_id=None,
                    host_id=host_id,
                    lease_type=LeaseType.MAINTENANCE.value,
                    status=LeaseStatus.ACTIVE.value,
                    fencing_token=f"{device.id}:1",
                    lease_generation=1,
                    agent_instance_id="platform",
                    holder=DEBT_LEASE_HOLDER,
                    reason=DEBT_LEASE_REASON,
                    acquired_at=now,
                    renewed_at=now,
                    expires_at=datetime(9999, 1, 1, tzinfo=timezone.utc),
                )
            )
        db_session.commit()

    _add_busy_with_lease(2, "a")
    with _SqlCountProbe(engine) as probe_small:
        r1 = client.get(
            f"/api/v1/devices?q=N13904-&projection=list&skip=0&limit=50",
            headers=auth_headers,
        )
    assert r1.status_code == 200
    small_count = len(probe_small.statements)

    _add_busy_with_lease(10, "b")
    with _SqlCountProbe(engine) as probe_large:
        r2 = client.get(
            f"/api/v1/devices?q=N13904-&projection=list&skip=0&limit=50",
            headers=auth_headers,
        )
    assert r2.status_code == 200
    assert len(r2.json()["items"]) >= len(r1.json()["items"]) + 10
    assert len(probe_large.statements) == small_count, (
        f"SQL count grew with rows: small={small_count} "
        f"large={len(probe_large.statements)}"
    )
    for item in r2.json()["items"]:
        if str(item["serial"]).startswith("N13904-"):
            assert item["active_lease_type"] == "MAINTENANCE"
            assert item["active_lease_reason"] == DEBT_LEASE_REASON
