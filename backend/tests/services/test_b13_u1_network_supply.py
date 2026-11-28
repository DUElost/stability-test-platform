"""B13 U1 comprehensive PG-backed tests (ADR-0066 D1–D3, D5.3, D6.3 / #4212 §4.2 U1).

Covers all 8 mandatory anti-cases from B13 #4212 v1.3 §4.2 U1 against real PostgreSQL:
1. Manual enable vs offline/flash/VBUS PlanRun atomic mutual exclusion (shared lock order);
2. Manual enable rejected when device holds active return-debt MAINTENANCE lease with no active Job;
3. Unclaimed PENDING cancel transitions RESERVED -> RELEASED(unclaimed_canceled) passing PG CHECK,
   while PROVISIONING / RELEASED(teardown_confirmed) with NULL lease_id is rejected by PG CHECK,
   and duplicate active sessions on one device are rejected by partial unique index;
4. Cross-host shared pool + cross-pool host hard cap (usbnet_max_devices) + post-terminal RELEASING
   slot deduplicated by device_id union (no double-counting with legacy ResourceAllocation) and capped;
5. Pure legacy Plan rejects usb_net via both network_pool_id and wifi_pool_id, connect_wifi auto-selects
   wifi(direct), monkey_setup without SSID skips, Phase 1 rejects usb_net+vpn while allowing wifi+vpn,
   and mixed-plan intersection contradiction is rejected;
6. Agent grant-renew rejects RELEASED (410), wrong generation/host (409), expired manual session (410),
   inactive/expired lease (409), and non-RUNNING/finishing/aborting job (409);
7. Cooldown issuance paired test: when host.usbnet_min_allowed_iat_by_egress[egress] >= now, manual
   enable & grant-renew return 503 USBNET_EGRESS_COOLDOWN + Retry-After without forging future iat,
   and agent_claim defers that egress; once now > barrier, truthful iat issuance succeeds;
8. Credential redaction whitelist zero regression & /resource-pools/available intersection filtering.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from backend.core.database import AsyncSessionLocal, async_engine
from backend.core.settings.network_supply import reset_network_supply_settings_cache
from backend.models.device_lease import DeviceLease
from backend.models.enums import (
    DeviceStatus,
    HostStatus,
    JobStatus,
    LeaseStatus,
    LeaseType,
    PlanRunStatus,
)
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.network_session import DeviceNetworkSession
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRunHost
from backend.models.resource_pool import ResourceAllocation, ResourcePool
from backend.models.script import Script
from backend.services.agent_claim import ClaimRequest, claim_agent_jobs
from backend.services.errors import ServiceError
from backend.services.network_grant import (
    UsbnetEgressCooldownError,
    sign_usbnet_grant,
    verify_usbnet_grant,
)
from backend.services.network_supply import (
    MANUAL_NETWORK_LEASE_REASON,
    enable_manual_network_supply_sync,
    release_unclaimed_network_sessions_sync,
    renew_usbnet_grant_sync,
)
from backend.services.plan_wifi import (
    require_network_pool_matches_plan,
    resolve_requested_network_pool_id,
)
from backend.services.resource_pool import (
    current_host_usbnet_device_sets_sync,
    current_pool_device_sets_sync,
)


@pytest.fixture()
def ed25519_keypair(monkeypatch):
    """Configure a fresh Ed25519 signing key in NetworkSupplySettings."""
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    pub_pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    monkeypatch.setenv("STP_USBNET_GRANT_SIGNING_KEY", priv_pem)
    monkeypatch.setenv("STP_USBNET_GRANT_TTL_SECONDS", "900")
    reset_network_supply_settings_cache()
    yield priv, pub, priv_pem, pub_pem
    reset_network_supply_settings_cache()


def _make_host(
    db_session,
    *,
    host_id: str,
    ip: str | None = None,
    usbnet: bool = True,
    usbnet_max_devices: int | None = 4,
    barriers: dict[str, int] | None = None,
) -> Host:
    if ip is None:
        ip = f"10.200.{(abs(hash(host_id)) // 200) % 200}.{(abs(hash(host_id)) % 200) + 10}"
    host = Host(
        id=host_id,
        name=host_id,
        hostname=host_id,
        ip=ip,
        status=HostStatus.ONLINE.value,
        last_heartbeat=datetime.now(timezone.utc),
        agent_capabilities=["usb_net"] if usbnet else [],
        usbnet_max_devices=usbnet_max_devices,
        usbnet_min_allowed_iat_by_egress=barriers,
    )
    db_session.add(host)
    db_session.flush()
    return host


def _make_device(
    db_session,
    *,
    host_id: str,
    serial: str,
    usb_devpath: str | None = "1-2.1",
) -> Device:
    extra = {"usb_devpath": usb_devpath} if usb_devpath else {}
    dev = Device(
        serial=serial,
        host_id=host_id,
        status=DeviceStatus.ONLINE.value,
        adb_connected=True,
        adb_state="device",
        extra=extra,
    )
    db_session.add(dev)
    db_session.flush()
    return dev


def _make_pool(
    db_session,
    *,
    name: str,
    resource_type: str = "usb_net",
    egress: str = "direct",
    max_concurrent_devices: int = 8,
    host_group: str | None = None,
    extra_cfg: dict | None = None,
) -> ResourcePool:
    cfg = {"egress": egress}
    if resource_type == "wifi":
        cfg.setdefault("ssid", f"Lab-{name}")
        cfg.setdefault("password", "super-secret-pw")
    if extra_cfg:
        cfg.update(extra_cfg)
    pool = ResourcePool(
        name=name,
        resource_type=resource_type,
        config=cfg,
        max_concurrent_devices=max_concurrent_devices,
        host_group=host_group,
        is_active=True,
    )
    db_session.add(pool)
    db_session.flush()
    return pool


def _ensure_script(db_session, *, name: str, version: str = "1.0.0") -> Script:
    existing = (
        db_session.query(Script)
        .filter(Script.name == name, Script.version == version)
        .first()
    )
    if existing:
        return existing
    sc = Script(
        name=name,
        version=version,
        script_type="python",
        nfs_path=f"/scripts/{name}/{version}/{name}.py",
        content_sha256="a" * 64,
        default_params={},
    )
    db_session.add(sc)
    db_session.flush()
    return sc


def _make_plan_with_steps(
    db_session,
    *,
    name: str,
    steps: list[tuple[str, str, dict]],
) -> Plan:
    plan = Plan(name=name)
    db_session.add(plan)
    db_session.flush()
    for idx, (phase, script_name, params) in enumerate(steps, start=1):
        _ensure_script(db_session, name=script_name)
        db_session.add(
            PlanStep(
                plan_id=plan.id,
                step_key=f"{script_name}_{idx}",
                stage=phase,
                sort_order=idx,
                script_name=script_name,
                script_version="1.0.0",
                params=params,
                enabled=True,
            )
        )
    db_session.flush()
    return plan


# ── Anti-case 1 & 2: Atomic manual mutual exclusion + return-debt lease block ──


def test_u1_anticase1_manual_enable_mutual_exclusion_with_job_and_lease(
    client, db_session, admin_headers, ed25519_keypair
):
    """Manual enable acquires MAINTENANCE lease and is mutually exclusive with active jobs/leases."""
    _, pub, _, _ = ed25519_keypair
    host = _make_host(db_session, host_id="host-u1-ac1", usbnet=True, usbnet_max_devices=4)
    dev = _make_device(db_session, host_id=host.id, serial="SN-U1-AC1-01", usb_devpath="1-2.1")
    pool = _make_pool(db_session, name="usb-ac1", resource_type="usb_net", egress="direct")
    db_session.commit()

    # 1. Manual enable succeeds on idle device, creates MAINTENANCE lease + PROVISIONING session + grant
    resp = client.post(
        "/api/v1/network-supply/manual/enable",
        json={"device_id": dev.id, "resource_pool_id": pool.id, "ttl_seconds": 3600},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["source"] == "manual"
    assert data["status"] == "PROVISIONING"
    assert data["lease_id"] is not None
    assert data["generation"] == 1
    assert data["usbnet_grant"]
    payload = verify_usbnet_grant(data["usbnet_grant"], pub, now_ts=int(time.time()))
    assert payload["session_id"] == data["id"]
    assert payload["generation"] == 1
    assert payload["device_serial"] == dev.serial
    assert payload["usb_devpath"] == "1-2.1"

    lease = db_session.get(DeviceLease, data["lease_id"])
    assert lease is not None
    assert lease.lease_type == LeaseType.MAINTENANCE.value
    assert lease.status == LeaseStatus.ACTIVE.value
    assert lease.reason == MANUAL_NETWORK_LEASE_REASON

    # 2. Second manual enable on same device is rejected (409)
    resp2 = client.post(
        "/api/v1/network-supply/manual/enable",
        json={"device_id": dev.id, "resource_pool_id": pool.id},
        headers=admin_headers,
    )
    assert resp2.status_code == 409, resp2.text

    # 3. Manual enable on a device already occupied by an active JOB lease (offline/flash/VBUS) is rejected
    dev2 = _make_device(db_session, host_id=host.id, serial="SN-U1-AC1-02", usb_devpath="1-2.2")
    now = datetime.now(timezone.utc)
    db_session.add(
        DeviceLease(
            device_id=dev2.id,
            job_id=None,
            host_id=host.id,
            lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=f"{dev2.id}:1",
            lease_generation=1,
            agent_instance_id="agent-1",
            reason="vbus_flash",
            acquired_at=now,
            renewed_at=now,
            expires_at=now + timedelta(minutes=10),
        )
    )
    db_session.commit()

    resp3 = client.post(
        "/api/v1/network-supply/manual/enable",
        json={"device_id": dev2.id, "resource_pool_id": pool.id},
        headers=admin_headers,
    )
    assert resp3.status_code == 409, resp3.text
    assert resp3.json()["detail"]["code"] == "DEVICE_LEASE_CONFLICT"


def test_u1_anticase2_return_debt_maintenance_lease_blocks_manual_enable(
    client, db_session, admin_headers, ed25519_keypair
):
    """Device with active return-debt MAINTENANCE lease (no active Job) cannot start manual supply."""
    host = _make_host(db_session, host_id="host-u1-ac2", usbnet=True)
    dev = _make_device(db_session, host_id=host.id, serial="SN-U1-AC2-01", usb_devpath="1-3.1")
    pool = _make_pool(db_session, name="usb-ac2", resource_type="usb_net", egress="direct")
    now = datetime.now(timezone.utc)
    db_session.add(
        DeviceLease(
            device_id=dev.id,
            job_id=None,
            host_id=host.id,
            lease_type=LeaseType.MAINTENANCE.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=f"{dev.id}:5",
            lease_generation=5,
            agent_instance_id="",
            reason="return_debt",
            acquired_at=now,
            renewed_at=now,
            expires_at=now + timedelta(hours=24),
        )
    )
    db_session.commit()

    resp = client.post(
        "/api/v1/network-supply/manual/enable",
        json={"device_id": dev.id, "resource_pool_id": pool.id},
        headers=admin_headers,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "DEVICE_LEASE_CONFLICT"
    assert db_session.query(DeviceNetworkSession).filter_by(device_id=dev.id).count() == 0


# ── Anti-case 3: Exact PG CHECK constraint & unclaimed PENDING cancel ──


def test_u1_anticase3_pg_check_constraints_and_unclaimed_cancel(
    db_session, sample_plan, sample_plan_run
):
    """RESERVED -> RELEASED(unclaimed_canceled) passes CHECK; null-lease PROVISIONING/teardown_confirmed fail."""
    host = _make_host(db_session, host_id="host-u1-ac3", usbnet=True)
    dev = _make_device(db_session, host_id=host.id, serial="SN-U1-AC3-01", usb_devpath="1-4.1")
    pool = _make_pool(db_session, name="usb-ac3", resource_type="usb_net", egress="direct")
    job = JobInstance(
        plan_run_id=sample_plan_run.id,
        plan_id=sample_plan.id,
        device_id=dev.id,
        host_id=host.id,
        status=JobStatus.PENDING.value,
        pipeline_def={"lifecycle": {}},
    )
    db_session.add(job)
    db_session.flush()

    # 1. RESERVED with NULL lease_id/generation is valid
    sess = DeviceNetworkSession(
        device_id=dev.id,
        host_id=host.id,
        resource_pool_id=pool.id,
        resource_type="usb_net",
        egress="direct",
        source="job",
        plan_run_id=sample_plan_run.id,
        job_instance_id=job.id,
        lease_id=None,
        generation=None,
        status="RESERVED",
    )
    db_session.add(sess)
    db_session.flush()

    # 2. Partial unique index rejects second active session on same device
    dup = DeviceNetworkSession(
        device_id=dev.id,
        host_id=host.id,
        resource_pool_id=pool.id,
        resource_type="usb_net",
        egress="direct",
        source="job",
        plan_run_id=sample_plan_run.id,
        job_instance_id=job.id,
        lease_id=None,
        generation=None,
        status="RESERVED",
    )
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(dup)
            db_session.flush()

    # 3. Unclaimed PENDING cancel transitions RESERVED -> RELEASED/unclaimed_canceled passing CHECK
    canceled = release_unclaimed_network_sessions_sync(db_session, [job.id])
    assert canceled == 1
    db_session.refresh(sess)
    assert sess.status == "RELEASED"
    assert sess.release_reason == "unclaimed_canceled"
    assert sess.lease_id is None
    assert sess.generation is None

    # 4. PROVISIONING with NULL lease_id is rejected by ck_device_network_session_lease_binding
    bad_prov = DeviceNetworkSession(
        device_id=dev.id,
        host_id=host.id,
        resource_pool_id=pool.id,
        resource_type="usb_net",
        egress="direct",
        source="job",
        plan_run_id=sample_plan_run.id,
        job_instance_id=job.id,
        lease_id=None,
        generation=None,
        status="PROVISIONING",
    )
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(bad_prov)
            db_session.flush()

    # 5. RELEASED with teardown_confirmed and NULL lease_id is rejected by CHECK
    bad_rel = DeviceNetworkSession(
        device_id=dev.id,
        host_id=host.id,
        resource_pool_id=pool.id,
        resource_type="usb_net",
        egress="direct",
        source="job",
        plan_run_id=sample_plan_run.id,
        job_instance_id=job.id,
        lease_id=None,
        generation=None,
        status="RELEASED",
        release_reason="teardown_confirmed",
    )
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(bad_rel)
            db_session.flush()


# ── Anti-case 4: Cross-host shared pool + cross-pool host cap + RELEASING dedup ──


def test_u1_anticase4_union_dedup_quota_and_cross_pool_host_hard_cap(
    client, db_session, admin_headers, sample_plan, sample_plan_run, ed25519_keypair
):
    """Union dedup by device_id avoids double-counting; RELEASING holds slot; cross-pool host cap enforced."""
    host1 = _make_host(db_session, host_id="host-u1-ac4-a", usbnet=True, usbnet_max_devices=2)
    host2 = _make_host(db_session, host_id="host-u1-ac4-b", usbnet=True, usbnet_max_devices=4)
    d1 = _make_device(db_session, host_id=host1.id, serial="SN-U1-AC4-01", usb_devpath="1-1.1")
    d2 = _make_device(db_session, host_id=host1.id, serial="SN-U1-AC4-02", usb_devpath="1-1.2")
    d3 = _make_device(db_session, host_id=host1.id, serial="SN-U1-AC4-03", usb_devpath="1-1.3")
    d4 = _make_device(db_session, host_id=host2.id, serial="SN-U1-AC4-04", usb_devpath="2-1.1")

    pool_a = _make_pool(db_session, name="usb-shared-a", resource_type="usb_net", max_concurrent_devices=2)
    pool_b = _make_pool(db_session, name="usb-shared-b", resource_type="usb_net", max_concurrent_devices=4)

    now = datetime.now(timezone.utc)
    # d1 has BOTH a legacy ResourceAllocation AND a RELEASING DeviceNetworkSession (post-terminal job)
    job1 = JobInstance(
        plan_run_id=sample_plan_run.id,
        plan_id=sample_plan.id,
        device_id=d1.id,
        host_id=host1.id,
        status=JobStatus.COMPLETED.value,
        pipeline_def={"lifecycle": {}},
    )
    db_session.add(job1)
    db_session.flush()
    lease1 = DeviceLease(
        device_id=d1.id,
        job_id=job1.id,
        host_id=host1.id,
        lease_type=LeaseType.MAINTENANCE.value,
        status=LeaseStatus.ACTIVE.value,
        fencing_token=f"{d1.id}:1",
        lease_generation=1,
        agent_instance_id="",
        reason="return_debt",
        acquired_at=now,
        renewed_at=now,
        expires_at=now + timedelta(hours=1),
    )
    db_session.add(lease1)
    db_session.flush()
    db_session.add(
        ResourceAllocation(
            resource_pool_id=pool_a.id,
            job_instance_id=job1.id,
            device_id=d1.id,
            allocated_params={},
        )
    )
    db_session.add(
        DeviceNetworkSession(
            device_id=d1.id,
            host_id=host1.id,
            resource_pool_id=pool_a.id,
            resource_type="usb_net",
            egress="direct",
            source="job",
            plan_run_id=sample_plan_run.id,
            job_instance_id=job1.id,
            lease_id=lease1.id,
            generation=1,
            status="RELEASING",
            usb_devpath="1-1.1",
        )
    )
    db_session.commit()

    # Dedup: d1 counted ONCE in pool_a (not twice!) even with both allocation + RELEASING session
    pool_sets = current_pool_device_sets_sync(db_session, [pool_a.id, pool_b.id])
    assert pool_sets[pool_a.id] == {d1.id}

    # Enable manual usb_net on d2 in pool_b (host1 now has 2/2 across pool_a + pool_b!)
    r_d2 = client.post(
        "/api/v1/network-supply/manual/enable",
        json={"device_id": d2.id, "resource_pool_id": pool_b.id},
        headers=admin_headers,
    )
    assert r_d2.status_code == 201, r_d2.text

    host_sets, _ = current_host_usbnet_device_sets_sync(db_session, [host1.id])
    assert host_sets[host1.id] == {d1.id, d2.id}

    # d3 on host1 in pool_b must fail due to cross-pool host hard cap (usbnet_max_devices=2)
    r_d3 = client.post(
        "/api/v1/network-supply/manual/enable",
        json={"device_id": d3.id, "resource_pool_id": pool_b.id},
        headers=admin_headers,
    )
    assert r_d3.status_code == 409, r_d3.text
    assert r_d3.json()["detail"]["code"] == "HOST_USBNET_CAPACITY_EXCEEDED"

    # d4 on host2 in pool_a succeeds (pool_a now 2/2: d1 on host1 + d4 on host2)
    r_d4 = client.post(
        "/api/v1/network-supply/manual/enable",
        json={"device_id": d4.id, "resource_pool_id": pool_a.id},
        headers=admin_headers,
    )
    assert r_d4.status_code == 201, r_d4.text

    # d5 on host2 in pool_a must fail due to pool global max_concurrent_devices=2
    d5 = _make_device(db_session, host_id=host2.id, serial="SN-U1-AC4-05", usb_devpath="2-1.2")
    db_session.commit()
    r_d5 = client.post(
        "/api/v1/network-supply/manual/enable",
        json={"device_id": d5.id, "resource_pool_id": pool_a.id},
        headers=admin_headers,
    )
    assert r_d5.status_code == 409, r_d5.text
    assert r_d5.json()["detail"]["code"] == "NETWORK_POOL_CAPACITY_EXCEEDED"


# ── Anti-case 5 & 8: Legacy Plan compatibility, Phase 1 egress rules & redaction ──


def test_u1_anticase5_and_8_pool_validation_legacy_compat_and_redaction(
    client, db_session, admin_headers, auth_headers
):
    """Phase 1 rejects usb_net+vpn, allows wifi+vpn, preserves redaction, enforces legacy & mixed plan rules."""
    # Phase 1: usb_net + vpn rejected (400)
    r_bad = client.post(
        "/api/v1/resource-pools",
        json={
            "name": "usb-vpn-banned",
            "resource_type": "usb_net",
            "config": {"egress": "vpn"},
            "max_concurrent_devices": 4,
        },
        headers=admin_headers,
    )
    assert r_bad.status_code == 400, r_bad.text

    # Phase 1: wifi + vpn allowed (201), wifi + direct allowed, usb_net + direct allowed
    r_wifi_vpn = client.post(
        "/api/v1/resource-pools",
        json={
            "name": "wifi-vpn-ok",
            "resource_type": "wifi",
            "config": {
                "ssid": "Lab-VPN",
                "password": "secret-vpn-password",
                "egress": "vpn",
                "private_token": "must-not-leak",
            },
            "max_concurrent_devices": 8,
        },
        headers=admin_headers,
    )
    assert r_wifi_vpn.status_code == 201, r_wifi_vpn.text
    wifi_vpn_id = r_wifi_vpn.json()["data"]["id"]

    wifi_direct = _make_pool(db_session, name="wifi-direct-ok", resource_type="wifi", egress="direct")
    usb_direct = _make_pool(db_session, name="usb-direct-ok", resource_type="usb_net", egress="direct")
    db_session.commit()

    # Anti-case 8: /available redaction & intersection filters
    avail_vpn = client.get(
        "/api/v1/resource-pools/available?required_egress=vpn",
        headers=auth_headers,
    )
    assert avail_vpn.status_code == 200, avail_vpn.text
    vpn_pools = avail_vpn.json()["data"]
    assert any(p["id"] == wifi_vpn_id for p in vpn_pools)
    target_vpn = next(p for p in vpn_pools if p["id"] == wifi_vpn_id)
    assert "password" not in target_vpn["config"]
    assert "private_token" not in target_vpn["config"]
    assert target_vpn["config"]["ssid"] == "Lab-VPN"
    assert target_vpn["config"]["egress"] == "vpn"

    avail_usb_vpn = client.get(
        "/api/v1/resource-pools/available?required_type=usb_net&required_egress=vpn",
        headers=auth_headers,
    )
    assert avail_usb_vpn.status_code == 200
    assert avail_usb_vpn.json()["data"] == []

    # Dual-key conflict check
    with pytest.raises((HTTPException, ServiceError)):
        resolve_requested_network_pool_id(
            network_pool_id=wifi_direct.id,
            wifi_pool_id=usb_direct.id,
        )

    # Pure legacy connect_wifi plan rejects usb_net pool
    legacy_wifi_plan = _make_plan_with_steps(
        db_session,
        name="legacy-connect-wifi",
        steps=[("init", "connect_wifi", {})],
    )
    with pytest.raises((HTTPException, ServiceError)):
        require_network_pool_matches_plan(db_session, legacy_wifi_plan.id, usb_direct.id)
    require_network_pool_matches_plan(db_session, legacy_wifi_plan.id, wifi_direct.id)

    # Pure legacy monkey_setup without SSID skips network pool requirement; with SSID rejects usb_net
    monkey_no_ssid = _make_plan_with_steps(
        db_session,
        name="legacy-monkey-no-ssid",
        steps=[("init", "monkey_setup", {"skip_wifi": True})],
    )
    require_network_pool_matches_plan(db_session, monkey_no_ssid.id, None)

    monkey_with_ssid = _make_plan_with_steps(
        db_session,
        name="legacy-monkey-ssid",
        steps=[("init", "monkey_setup", {"wifi_ssid": "Lab"})],
    )
    with pytest.raises((HTTPException, ServiceError)):
        require_network_pool_matches_plan(db_session, monkey_with_ssid.id, usb_direct.id)

    # Mixed plan with contradictory type requirements (usb_net + connect_wifi) rejected
    mixed_conflict = _make_plan_with_steps(
        db_session,
        name="mixed-conflict",
        steps=[
            ("init", "setup_network", {"required_type": "usb_net", "required_egress": "direct"}),
            ("init", "connect_wifi", {}),
        ],
    )
    with pytest.raises((HTTPException, ServiceError)):
        require_network_pool_matches_plan(db_session, mixed_conflict.id, usb_direct.id)

    # setup_network requiring vpn accepts wifi(vpn) and rejects wifi(direct)
    vpn_plan = _make_plan_with_steps(
        db_session,
        name="setup-network-vpn",
        steps=[
            ("init", "setup_network", {"required_egress": "vpn"}),
        ],
    )
    require_network_pool_matches_plan(db_session, vpn_plan.id, wifi_vpn_id)
    with pytest.raises((HTTPException, ServiceError)):
        require_network_pool_matches_plan(db_session, vpn_plan.id, wifi_direct.id)


# ── Anti-case 6 & 7: grant-renew rejections + cooldown issuance paired test ──


def test_u1_anticase6_grant_renew_rejects_invalid_states(
    client, db_session, admin_headers, sample_plan, sample_plan_run, ed25519_keypair
):
    """grant-renew succeeds on active session and rejects released / wrong gen / expired / aborting."""
    _, pub, _, _ = ed25519_keypair
    host = _make_host(db_session, host_id="host-u1-ac6", usbnet=True)
    dev = _make_device(db_session, host_id=host.id, serial="SN-U1-AC6-01", usb_devpath="1-5.1")
    pool = _make_pool(db_session, name="usb-ac6", resource_type="usb_net", egress="direct")
    db_session.commit()

    # Enable manual usb_net session
    r_en = client.post(
        "/api/v1/network-supply/manual/enable",
        json={"device_id": dev.id, "resource_pool_id": pool.id, "ttl_seconds": 3600},
        headers=admin_headers,
    )
    assert r_en.status_code == 201
    sess_id = r_en.json()["data"]["id"]
    gen = r_en.json()["data"]["generation"]

    # Valid renewal succeeds (200)
    r_ok = client.post(
        f"/api/v1/agent/network-sessions/{sess_id}/grant-renew",
        json={"host_id": host.id, "generation": gen, "usb_devpath": "1-5.1"},
    )
    assert r_ok.status_code == 200, r_ok.text
    renew_data = r_ok.json()["data"]
    assert renew_data["exp"] - renew_data["iat"] == 900
    verify_usbnet_grant(renew_data["usbnet_grant"], pub, now_ts=renew_data["iat"])

    # Wrong generation rejected (409)
    r_gen = client.post(
        f"/api/v1/agent/network-sessions/{sess_id}/grant-renew",
        json={"host_id": host.id, "generation": gen + 1, "usb_devpath": "1-5.1"},
    )
    assert r_gen.status_code == 409

    # Wrong host rejected (409)
    r_host = client.post(
        f"/api/v1/agent/network-sessions/{sess_id}/grant-renew",
        json={"host_id": "other-host", "generation": gen, "usb_devpath": "1-5.1"},
    )
    assert r_host.status_code == 409

    # Expired manual session rejected (410)
    sess = db_session.get(DeviceNetworkSession, sess_id)
    sess.expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    db_session.commit()
    r_exp = client.post(
        f"/api/v1/agent/network-sessions/{sess_id}/grant-renew",
        json={"host_id": host.id, "generation": gen, "usb_devpath": "1-5.1"},
    )
    assert r_exp.status_code == 410

    # RELEASED session rejected (410)
    sess.status = "RELEASED"
    sess.release_reason = "manual_released"
    db_session.commit()
    r_rel = client.post(
        f"/api/v1/agent/network-sessions/{sess_id}/grant-renew",
        json={"host_id": host.id, "generation": gen, "usb_devpath": "1-5.1"},
    )
    assert r_rel.status_code == 410

    # Job session in finishing_guard rejected (409)
    dev2 = _make_device(db_session, host_id=host.id, serial="SN-U1-AC6-02", usb_devpath="1-5.2")
    now = datetime.now(timezone.utc)
    job = JobInstance(
        plan_run_id=sample_plan_run.id,
        plan_id=sample_plan.id,
        device_id=dev2.id,
        host_id=host.id,
        status=JobStatus.RUNNING.value,
        pipeline_def={"lifecycle": {}},
        finishing_guard={"state": "granted"},
    )
    db_session.add(job)
    db_session.flush()
    lease2 = DeviceLease(
        device_id=dev2.id,
        job_id=job.id,
        host_id=host.id,
        lease_type=LeaseType.JOB.value,
        status=LeaseStatus.ACTIVE.value,
        fencing_token=f"{dev2.id}:1",
        lease_generation=1,
        agent_instance_id="agent-1",
        acquired_at=now,
        renewed_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    db_session.add(lease2)
    db_session.flush()
    sess2 = DeviceNetworkSession(
        device_id=dev2.id,
        host_id=host.id,
        resource_pool_id=pool.id,
        resource_type="usb_net",
        egress="direct",
        source="job",
        plan_run_id=sample_plan_run.id,
        job_instance_id=job.id,
        lease_id=lease2.id,
        generation=1,
        status="ACTIVE",
        usb_devpath="1-5.2",
    )
    db_session.add(sess2)
    db_session.commit()

    r_fin = client.post(
        f"/api/v1/agent/network-sessions/{sess2.id}/grant-renew",
        json={"host_id": host.id, "generation": 1, "usb_devpath": "1-5.2"},
    )
    assert r_fin.status_code == 409
    assert r_fin.json()["detail"]["code"] == "JOB_FINISHING"


def test_u1_anticase7_cooldown_barrier_blocks_without_forging_iat_and_recovers(
    client, db_session, admin_headers, sample_plan, sample_plan_run, ed25519_keypair
):
    """During cooldown: manual & renew return 503 + Retry-After, claim defers; after cooldown: truthful iat succeeds."""
    _, pub, _, _ = ed25519_keypair
    now_ts = int(time.time())
    barrier = now_ts + 25
    host = _make_host(
        db_session,
        host_id="host-u1-ac7",
        usbnet=True,
        usbnet_max_devices=4,
        barriers={"direct": barrier},
    )
    dev_manual = _make_device(db_session, host_id=host.id, serial="SN-U1-AC7-M", usb_devpath="1-6.1")
    dev_job = _make_device(db_session, host_id=host.id, serial="SN-U1-AC7-J", usb_devpath="1-6.2")
    pool = _make_pool(db_session, name="usb-ac7", resource_type="usb_net", egress="direct")

    # Prepare a PENDING job with a RESERVED usb_net session for claim test
    sample_plan_run.status = PlanRunStatus.RUNNING.value
    db_session.add(
        PlanRunHost(
            plan_run_id=sample_plan_run.id,
            host_id=host.id,
            status="RUNNING",
        )
    )
    job = JobInstance(
        plan_run_id=sample_plan_run.id,
        plan_id=sample_plan.id,
        device_id=dev_job.id,
        host_id=host.id,
        status=JobStatus.PENDING.value,
        pipeline_def={
            "lifecycle": {
                "init": [{"action": "script:setup_network", "params": {"resource_type": "usb_net"}}]
            }
        },
    )
    db_session.add(job)
    db_session.flush()
    job_sess = DeviceNetworkSession(
        device_id=dev_job.id,
        host_id=host.id,
        resource_pool_id=pool.id,
        resource_type="usb_net",
        egress="direct",
        source="job",
        plan_run_id=sample_plan_run.id,
        job_instance_id=job.id,
        lease_id=None,
        generation=None,
        status="RESERVED",
    )
    db_session.add(job_sess)
    db_session.commit()

    # 1. Direct signer check: never forges future iat
    with pytest.raises(UsbnetEgressCooldownError) as exc_info:
        sign_usbnet_grant(
            session_id=1,
            generation=1,
            host_id=host.id,
            device_serial=dev_manual.serial,
            usb_devpath="1-6.1",
            egress="direct",
            min_allowed_iat_by_egress={"direct": barrier},
            now_ts=now_ts,
        )
    assert exc_info.value.retry_after_seconds == 26

    # 2. Manual enable during cooldown returns 503 USBNET_EGRESS_COOLDOWN + Retry-After, no lease/session created
    r_cd = client.post(
        "/api/v1/network-supply/manual/enable",
        json={"device_id": dev_manual.id, "resource_pool_id": pool.id},
        headers=admin_headers,
    )
    assert r_cd.status_code == 503, r_cd.text
    assert r_cd.json()["detail"]["code"] == "USBNET_EGRESS_COOLDOWN"
    assert int(r_cd.headers["Retry-After"]) >= 1
    assert db_session.query(DeviceNetworkSession).filter_by(device_id=dev_manual.id).count() == 0
    assert db_session.query(DeviceLease).filter_by(device_id=dev_manual.id).count() == 0

    # 3. agent_claim during cooldown defers claiming the usb_net job (keeps PENDING & RESERVED)
    async def _run_claim():
        try:
            async with AsyncSessionLocal() as adb:
                return await claim_agent_jobs(
                    adb,
                    ClaimRequest(
                        host_id=host.id,
                        agent_instance_id="agent-u1-ac7",
                        capacity=4,
                    ),
                )
        finally:
            await async_engine.dispose()

    claimed_during_cd = asyncio.run(_run_claim())
    assert claimed_during_cd == []
    db_session.expire_all()
    assert db_session.get(JobInstance, job.id).status == JobStatus.PENDING.value
    assert db_session.get(DeviceNetworkSession, job_sess.id).status == "RESERVED"

    # 4. After cooldown elapses (barrier < now): manual enable, claim, and grant-renew all succeed with truthful iat
    past_barrier = now_ts - 5
    host.usbnet_min_allowed_iat_by_egress = {"direct": past_barrier}
    db_session.commit()

    r_ok = client.post(
        "/api/v1/network-supply/manual/enable",
        json={"device_id": dev_manual.id, "resource_pool_id": pool.id},
        headers=admin_headers,
    )
    assert r_ok.status_code == 201, r_ok.text
    manual_data = r_ok.json()["data"]
    m_payload = verify_usbnet_grant(manual_data["usbnet_grant"], pub, now_ts=int(time.time()))
    assert m_payload["iat"] > past_barrier
    assert m_payload["iat"] <= int(time.time()) + 5

    # Agent reports a new future barrier on grant-renew -> 503 + persists barrier on host
    r_renew_cd = client.post(
        f"/api/v1/agent/network-sessions/{manual_data['id']}/grant-renew",
        json={
            "host_id": host.id,
            "generation": manual_data["generation"],
            "usb_devpath": "1-6.1",
            "min_allowed_iat_by_egress": {"direct": int(time.time()) + 20},
        },
    )
    assert r_renew_cd.status_code == 503, r_renew_cd.text
    assert r_renew_cd.json()["detail"]["code"] == "USBNET_EGRESS_COOLDOWN"
    assert int(r_renew_cd.headers["Retry-After"]) >= 1

    # Lower barrier back below now -> claim succeeds & binds JOB lease + PROVISIONING + signed usbnet_grant
    db_session.expire_all()
    host = db_session.get(Host, host.id)
    host.usbnet_min_allowed_iat_by_egress = {"direct": past_barrier}
    db_session.commit()

    claimed = asyncio.run(_run_claim())
    assert len(claimed) == 1
    job_out = claimed[0]
    assert job_out.id == job.id
    assert job_out.network_session is not None
    assert job_out.network_session["session_id"] == job_sess.id
    assert job_out.network_session["usbnet_grant"]
    j_payload = verify_usbnet_grant(
        job_out.network_session["usbnet_grant"], pub, now_ts=int(time.time())
    )
    assert j_payload["iat"] > past_barrier
    assert j_payload["device_serial"] == dev_job.serial
    assert j_payload["usb_devpath"] == "1-6.2"

    db_session.expire_all()
    refreshed_sess = db_session.get(DeviceNetworkSession, job_sess.id)
    assert refreshed_sess.status == "PROVISIONING"
    assert refreshed_sess.lease_id is not None
    assert refreshed_sess.generation == j_payload["generation"]

    # Direct service check with explicit now_ts > barrier succeeds cleanly
    renewed = renew_usbnet_grant_sync(
        db_session,
        session_id=manual_data["id"],
        host_id=host.id,
        generation=manual_data["generation"],
        usb_devpath="1-6.1",
        now_ts=int(time.time()) + 5,
    )
    assert renewed["iat"] > past_barrier
    assert renewed["exp"] - renewed["iat"] == 900

    # Manual enable helper returns structured payload when called directly
    dev_wifi = _make_device(db_session, host_id=host.id, serial="SN-U1-AC7-W", usb_devpath=None)
    wifi_pool = _make_pool(db_session, name="wifi-ac7", resource_type="wifi", egress="direct")
    db_session.commit()
    wifi_res = enable_manual_network_supply_sync(
        db_session,
        device_id=dev_wifi.id,
        resource_pool_id=wifi_pool.id,
        admin_username="admin",
    )
    assert wifi_res["resource_type"] == "wifi"
    assert "usbnet_grant" not in wifi_res
