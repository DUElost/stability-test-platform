"""B13-U2 PostgreSQL integration tests: usb_net ADR-0061 return debt, late confirmed,
manual wifi/usb_net release loops, dedicated audit-override & expired manual reaper.

Covers all five mandatory anti-cases in Batch B13 #4212 v1.3 §4.2 U2 (ADR-0066 D2.6, D3.4, D4.4–D4.5):
  ① usb_net confirmed cleared while another family (power-cycle) still has open debt
    → device_network_session settles to RELEASED(teardown_confirmed), JOB lease converts to debt (N+1);
  ② Job usb_net unconfirmed (N → N+1 debt): discharge with N+1 + dual-side readback closes debt,
    session (debt_discharged) & debt lease; stale job token N returns DEBT_TOKEN_INVALID;
    missing dual-side evidence is rejected (422);
  ③ Legitimate late confirmed carrying original job token N closes usb_net debt, sets session to
    RELEASED(late_confirmed) and releases debt lease; device can be re-dispatched; replaying the
    same late payload is idempotent and does NOT touch a newer higher-generation session;
  ④ Manual wifi offline release creates ZERO DeviceReturnDebt rows, keeps RELEASE_FAILED and
    manual_network_supply lease at same generation N; heartbeat delivers pending release; both
    reconnect release-report(confirmed=True) and admin audit-override atomically close session & lease;
  ⑤ Manual usb_net unconfirmed release converts manual_network_supply (N) to ADR-0061 debt lease (N+1)
    + open usb_net debt; both discharge(N+1) and admin audit-override close session & lease;
    audit-override marks usb_net unverified(override), emits usbnet_revoke_directive & heartbeat
    pending_usbnet_revokes, and revoke-ack clears pending delivery; expired manual session reaper
    transitions expires_at <= now to RELEASING without releasing lease.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.agent.contracts.device_return import (
    DISARM_RESULT_CONFIRMED,
    DISARM_RESULT_FAILED,
    FAMILY_STEPS,
    ONBOARDED,
    validate_onboarded_teardown_shape,
)
from backend.core.database import AsyncSessionLocal, async_engine
from backend.core.settings.network_supply import reset_network_supply_settings_cache
from backend.models.audit import AuditLog
from backend.models.device_lease import DeviceLease
from backend.models.device_return_debt import DeviceReturnDebt
from backend.models.enums import (
    DeviceStatus,
    HostStatus,
    JobStatus,
    LeaseStatus,
    LeaseType,
    PlanRunStatus,
)
from backend.models.host import Device, Host
from backend.models.job import JobInstance, StepTrace
from backend.models.network_session import DeviceNetworkSession
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.resource_pool import ResourcePool
from backend.scheduler.network_session_reaper import (
    reap_expired_manual_network_sessions_sync,
)
from backend.services.agent_completion import _RunCompleteIn, complete_agent_job
from backend.services.device_return import (
    DEBT_LEASE_REASON,
    apply_discharge_sync,
    apply_late_confirmed_sync,
    handle_job_lease_release,
    handle_job_lease_release_sync,
    is_debt_lease,
    open_debts_for_host_sync,
)
from backend.services.errors import UnprocessableEntity
from backend.services.network_return import (
    MANUAL_NETWORK_LEASE_REASON,
    MANUAL_NETWORK_RETURN_SOURCE_KIND,
    audit_override_network_session_sync,
    pending_manual_network_releases_for_host_sync,
    pending_usbnet_revokes_for_host_sync,
    report_manual_network_release_sync,
)
from backend.services.network_supply import enable_manual_network_supply_sync


def _uniq(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def _usbnet_confirmed_entry() -> dict:
    return {
        "result": DISARM_RESULT_CONFIRMED,
        "step_id": "td_net",
        "exit_code": 0,
        "readback_summary": "adb_clear_bridge_detached",
        "evidence": {
            "mode": "disarm",
            "success": True,
            "readback": "clear",
            "kind": "ok",
            "device_usb_state": "adb",
            "host_bridge_detached": True,
            "script_name": "teardown_network",
            "script_version": "1.0.0",
        },
    }


@pytest.fixture
def ed25519_grant_key(monkeypatch):
    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    monkeypatch.setenv("STP_USBNET_GRANT_SIGNING_KEY", priv_pem)
    monkeypatch.setenv("STP_USBNET_GRANT_TTL_SECONDS", "900")
    reset_network_supply_settings_cache()
    yield priv
    reset_network_supply_settings_cache()


@pytest.fixture
def agent_headers():
    from backend.core.agent_secret import get_agent_secret

    return {"X-Agent-Secret": get_agent_secret()}


def _seed_host_device_pool(
    db,
    *,
    resource_type: str = "usb_net",
    egress: str = "direct",
    usb_devpath: str = "1-2.3",
):
    now = datetime.now(timezone.utc)
    host_id = _uniq("h-u2")
    serial = _uniq("DEVU2")
    host = Host(
        id=host_id,
        name=host_id,
        hostname=host_id,
        status=HostStatus.ONLINE.value,
        last_heartbeat=now,
        last_agent_instance_id=_uniq("agent"),
        agent_capabilities=["usb_net"] if resource_type == "usb_net" else [],
        usbnet_max_devices=8,
    )
    db.add(host)
    db.flush()

    device = Device(
        serial=serial,
        host_id=host.id,
        status=DeviceStatus.ONLINE.value,
        adb_connected=True,
        adb_state="device",
        platform="MTK",
        model="MLD_LX2",
        lease_generation=0,
        extra={"usb_devpath": usb_devpath} if resource_type == "usb_net" else {},
    )
    db.add(device)
    db.flush()

    pool_cfg = (
        {"egress": egress, "bridge_name": "br-stpusb-dir", "subnet_cidr": "10.200.0.0/22", "max_devices_per_host": 4}
        if resource_type == "usb_net"
        else {"egress": egress, "ssid": "LabWiFi", "password": "secret-pass"}
    )
    pool = ResourcePool(
        name=_uniq(f"pool-{resource_type}"),
        resource_type=resource_type,
        config=pool_cfg,
        max_concurrent_devices=8,
        is_active=True,
    )
    db.add(pool)
    db.flush()
    return host, device, pool


def _seed_job_with_session(
    db,
    host: Host,
    device: Device,
    pool: ResourcePool,
    *,
    disarm_results: dict | None = None,
    job_status: str = JobStatus.RUNNING.value,
    session_status: str = "ACTIVE",
    pipeline_def: dict | None = None,
):
    now = datetime.now(timezone.utc)
    plan = Plan(name=_uniq("plan-u2"))
    db.add(plan)
    db.flush()

    run = PlanRun(
        plan_id=plan.id,
        status=PlanRunStatus.RUNNING.value,
        plan_snapshot={"name": plan.name},
        run_type="MANUAL",
        triggered_by="test",
        started_at=now,
    )
    db.add(run)
    db.flush()

    default_pipeline = pipeline_def or {
        "lifecycle": {
            "init": [{"step_id": "init_net", "action": "script:setup_network", "version": "1.0.0"}],
            "teardown": [{"step_id": "td_net", "action": "script:teardown_network", "version": "1.0.0"}],
        }
    }
    job = JobInstance(
        plan_run_id=run.id,
        plan_id=plan.id,
        device_id=device.id,
        host_id=host.id,
        status=job_status,
        pipeline_def=default_pipeline,
        disarm_results=disarm_results,
        started_at=now,
    )
    db.add(job)
    db.flush()

    new_gen = int(device.lease_generation or 0) + 1
    device.lease_generation = new_gen
    lease = DeviceLease(
        device_id=device.id,
        job_id=job.id,
        host_id=host.id,
        lease_type=LeaseType.JOB.value,
        status=LeaseStatus.ACTIVE.value,
        fencing_token=f"{device.id}:{new_gen}",
        lease_generation=new_gen,
        agent_instance_id=host.last_agent_instance_id or "agent-u2",
        acquired_at=now,
        renewed_at=now,
        expires_at=now + timedelta(minutes=30),
    )
    db.add(lease)
    db.flush()

    sess = DeviceNetworkSession(
        device_id=device.id,
        host_id=host.id,
        resource_pool_id=pool.id,
        resource_type=pool.resource_type,
        egress=pool.config.get("egress", "direct"),
        source="job",
        plan_run_id=run.id,
        job_instance_id=job.id,
        lease_id=lease.id,
        generation=new_gen,
        legacy_path=False,
        status=session_status,
        usb_devpath=(device.extra or {}).get("usb_devpath"),
        created_at=now,
        updated_at=now,
    )
    db.add(sess)
    db.flush()
    return plan, run, job, lease, sess


# ── Contract registration checks ─────────────────────────────────────────────


def test_usb_net_onboarded_and_teardown_shape():
    assert "usb_net" in FAMILY_STEPS
    assert "usb_net" in ONBOARDED
    assert ONBOARDED["usb_net"]["disarm_script"] == "teardown_network"
    assert ONBOARDED["usb_net"]["disarm_min_version"] == "1.0.0"
    assert ONBOARDED["usb_net"]["init_check_enabled"] is False

    # Missing teardown_network violates Plan save contract (ADR-0066 D3.2)
    violations = validate_onboarded_teardown_shape(
        [{"step_key": "init_net", "script_name": "setup_network", "script_version": "1.0.0", "stage": "init", "enabled": True}]
    )
    assert any(v["family"] == "usb_net" and v["reason"] == "disarm_missing" for v in violations)

    # Paired teardown_network >= 1.0.0 passes cleanly
    ok_violations = validate_onboarded_teardown_shape(
        [
            {"step_key": "init_net", "script_name": "setup_network", "script_version": "1.0.0", "stage": "init", "enabled": True},
            {"step_key": "td_net", "script_name": "teardown_network", "script_version": "1.0.0", "stage": "teardown", "enabled": True},
        ]
    )
    assert ok_violations == []


# ── Anti-case ①: usb_net confirmed while power-cycle still has open debt ─────


def test_u2_anticase1_usbnet_confirmed_with_other_family_debt_keeps_debt_lease(db_session):
    host, device, pool = _seed_host_device_pool(db_session, resource_type="usb_net")
    _, _, job, lease, sess = _seed_job_with_session(
        db_session,
        host,
        device,
        pool,
        disarm_results={
            "usb_net": _usbnet_confirmed_entry(),
            "power-cycle": {"result": DISARM_RESULT_FAILED, "step_id": "td_pc"},
        },
    )
    orig_gen = lease.lease_generation
    db_session.commit()

    outcome = handle_job_lease_release_sync(
        db_session, device_id=device.id, job_id=job.id, job=job,
    )
    db_session.commit()

    db_session.refresh(sess)
    db_session.refresh(lease)
    db_session.refresh(device)

    # usb_net session settled to RELEASED(teardown_confirmed)
    assert sess.status == "RELEASED"
    assert sess.release_reason == "teardown_confirmed"
    assert sess.released_at is not None

    # JOB lease was NOT released early; converted in-place to debt lease (N → N+1)
    assert outcome.released is False
    assert outcome.converted_to_debt is True
    assert outcome.open_debt_count == 1
    assert is_debt_lease(lease)
    assert lease.status == LeaseStatus.ACTIVE.value
    assert lease.lease_generation == orig_gen + 1
    assert lease.fencing_token == f"{device.id}:{orig_gen + 1}"

    open_debts = (
        db_session.query(DeviceReturnDebt)
        .filter_by(device_id=device.id, state="open")
        .all()
    )
    assert [d.family for d in open_debts] == ["power-cycle"]
    assert device.return_state["usb_net"]["state"] == "returned"
    assert device.return_state["power-cycle"]["state"] == "debt"


def test_u2_wifi_job_with_setup_network_does_not_create_usbnet_debt(db_session):
    host, device, pool = _seed_host_device_pool(db_session, resource_type="wifi")
    _, _, job, lease, sess = _seed_job_with_session(
        db_session, host, device, pool, disarm_results=None,
    )
    # Record init StepTrace for setup_network (serving a wifi pool)
    db_session.add(
        StepTrace(
            job_id=job.id,
            step_id="init_net",
            stage="init",
            status="COMPLETED",
            event_type="STEP_END",
            original_ts=datetime.now(timezone.utc),
        )
    )
    db_session.commit()

    outcome = handle_job_lease_release_sync(
        db_session, device_id=device.id, job_id=job.id, job=job,
    )
    db_session.commit()

    db_session.refresh(sess)
    db_session.refresh(lease)
    assert outcome.released is True
    assert outcome.converted_to_debt is False
    assert outcome.open_debt_count == 0
    assert sess.status == "RELEASED"
    assert sess.release_reason == "teardown_confirmed"
    assert lease.status == LeaseStatus.RELEASED.value
    assert (
        db_session.query(DeviceReturnDebt)
        .filter_by(device_id=device.id, state="open")
        .count()
    ) == 0


# ── Anti-case ②: Job usb_net N → N+1 debt; N+1 discharges, stale N rejected ─


def test_u2_anticase2_usbnet_debt_n_plus_1_discharge_and_stale_n_rejected(db_session):
    host, device, pool = _seed_host_device_pool(db_session, resource_type="usb_net")
    # Simulate crash / missing disarm report while usb_net session is ACTIVE
    _, _, job, lease, sess = _seed_job_with_session(
        db_session, host, device, pool, disarm_results=None,
    )
    job_token_n = lease.fencing_token
    orig_gen = lease.lease_generation
    db_session.commit()

    outcome = handle_job_lease_release_sync(
        db_session, device_id=device.id, job_id=job.id, job=job,
    )
    db_session.commit()

    db_session.refresh(sess)
    db_session.refresh(lease)
    assert outcome.converted_to_debt is True
    assert outcome.open_debt_count == 1
    assert sess.status == "RELEASE_FAILED"
    assert is_debt_lease(lease)
    debt_token_n_plus_1 = lease.fencing_token
    assert debt_token_n_plus_1 == f"{device.id}:{orig_gen + 1}"

    # Heartbeat return_debts carries N+1 token & usbnet_sessions metadata
    hb_debts = open_debts_for_host_sync(db_session, host.id)
    assert len(hb_debts) == 1
    assert hb_debts[0]["fencing_token"] == debt_token_n_plus_1
    assert hb_debts[0]["families"] == ["usb_net"]
    assert hb_debts[0]["usbnet_sessions"] == [
        {
            "session_id": sess.id,
            "generation": orig_gen,
            "egress": "direct",
            "usb_devpath": "1-2.3",
        }
    ]

    # Old job token N MUST fail with DEBT_TOKEN_INVALID
    stale_res = apply_discharge_sync(
        db_session,
        device_id=device.id,
        fencing_token=job_token_n,
        family_results={"usb_net": _usbnet_confirmed_entry()},
    )
    assert stale_res == {"ok": False, "code": "DEBT_TOKEN_INVALID"}

    # Missing dual-side readback with valid N+1 token MUST be rejected (422)
    unverified_res = apply_discharge_sync(
        db_session,
        device_id=device.id,
        fencing_token=debt_token_n_plus_1,
        family_results={
            "usb_net": {
                "result": DISARM_RESULT_CONFIRMED,
                "evidence": {
                    "mode": "disarm",
                    "success": True,
                    "readback": "clear",
                    "kind": "ok",
                    "device_usb_state": "adb",
                    "host_bridge_detached": False,  # host side NOT confirmed!
                },
            }
        },
    )
    db_session.commit()
    assert unverified_res["discharged_families"] == []
    assert unverified_res["debt_lease_released"] is False
    assert len(unverified_res["rejected_families"]) == 1
    assert unverified_res["rejected_families"][0]["status_code"] == 422

    # Valid N+1 token + dual-side readback closes debt, session & debt lease atomically
    ok_res = apply_discharge_sync(
        db_session,
        device_id=device.id,
        fencing_token=debt_token_n_plus_1,
        family_results={"usb_net": _usbnet_confirmed_entry()},
    )
    db_session.commit()

    db_session.refresh(sess)
    db_session.refresh(lease)
    db_session.refresh(device)
    assert ok_res["ok"] is True
    assert ok_res["discharged_families"] == ["usb_net"]
    assert ok_res["debt_lease_released"] is True
    assert ok_res["open_debt_count"] == 0
    assert sess.status == "RELEASED"
    assert sess.release_reason == "debt_discharged"
    assert lease.status == LeaseStatus.RELEASED.value
    assert device.return_state["usb_net"]["state"] == "returned"


# ── Anti-case ③: Late confirmed (token N) closes debt/session/lease & replay idempotent ─


def test_u2_anticase3_late_confirmed_closes_session_and_replay_preserves_new_session(
    db_session,
):
    host, device, pool = _seed_host_device_pool(db_session, resource_type="usb_net")
    _, _, job1, lease1, sess1 = _seed_job_with_session(
        db_session,
        host,
        device,
        pool,
        job_status=JobStatus.FAILED.value,
        disarm_results={"usb_net": {"result": DISARM_RESULT_FAILED}},
    )
    job1_token_n = lease1.fencing_token
    db_session.commit()

    # Terminalize Job 1 unconfirmed → creates open usb_net debt + converts lease 1 → 2
    async def _run_release_and_late():
        try:
            async with AsyncSessionLocal() as adb:
                outcome = await handle_job_lease_release(
                    adb, device_id=device.id, job_id=job1.id,
                )
                await adb.commit()
                assert outcome.converted_to_debt is True

            # 1st legitimate late confirmed payload carrying original Job 1 token N
            late_payload = _RunCompleteIn(
                update={"status": "COMPLETED"},
                disarm_results={"usb_net": _usbnet_confirmed_entry()},
                fencing_token=job1_token_n,
            )
            async with AsyncSessionLocal() as adb:
                return await complete_agent_job(adb, job1.id, late_payload)
        finally:
            await async_engine.dispose()

    res1 = asyncio.run(_run_release_and_late())
    assert res1["late_confirmed"]["ok"] is True
    assert res1["late_confirmed"]["closed_count"] == 1
    assert res1["late_confirmed"]["debt_lease_released"] is True
    assert res1["late_confirmed"]["open_debt_count"] == 0

    db_session.expire_all()
    sess1_after = db_session.get(DeviceNetworkSession, sess1.id)
    lease1_after = db_session.get(DeviceLease, lease1.id)
    assert sess1_after.status == "RELEASED"
    assert sess1_after.release_reason == "late_confirmed"
    assert lease1_after.status == LeaseStatus.RELEASED.value

    # Device is now free: dispatch a brand-new Job 2 + lease 2 (gen=N+2) + session 2
    _, _, job2, lease2, sess2 = _seed_job_with_session(
        db_session, host, device, pool, session_status="ACTIVE",
    )
    assert sess2.generation > sess1.generation
    db_session.commit()

    # Replay the EXACT SAME late confirmed payload from Job 1 (token N)
    async def _replay_late():
        try:
            late_payload = _RunCompleteIn(
                update={"status": "COMPLETED"},
                disarm_results={"usb_net": _usbnet_confirmed_entry()},
                fencing_token=job1_token_n,
            )
            async with AsyncSessionLocal() as adb:
                return await complete_agent_job(adb, job1.id, late_payload)
        finally:
            await async_engine.dispose()

    res2 = asyncio.run(_replay_late())
    assert res2.get("idempotent") is True
    assert res2["late_confirmed"]["closed_count"] == 0

    # Sync helper replay is also strictly idempotent & never touches newer session 2
    sync_replay = apply_late_confirmed_sync(
        db_session,
        device_id=device.id,
        job_id=job1.id,
        fencing_token=job1_token_n,
        confirmed_families=["usb_net"],
    )
    db_session.commit()
    assert sync_replay["closed_count"] == 0
    assert sync_replay["debt_lease_released"] is False

    db_session.expire_all()
    sess2_after = db_session.get(DeviceNetworkSession, sess2.id)
    lease2_after = db_session.get(DeviceLease, lease2.id)
    assert sess2_after.status == "ACTIVE"
    assert sess2_after.release_reason is None
    assert lease2_after.status == LeaseStatus.ACTIVE.value


# ── Anti-case ④: Manual wifi offline release, reconnect callback & audit-override ─


def test_u2_anticase4_manual_wifi_offline_reconnect_and_audit_override(
    db_session, client, admin_headers, agent_headers,
):
    # Path A: offline release → RELEASE_FAILED (zero debt, same gen lease) → reconnect confirms
    host_a, dev_a, pool_a = _seed_host_device_pool(db_session, resource_type="wifi")
    db_session.commit()

    enabled_a = enable_manual_network_supply_sync(
        db_session,
        device_id=dev_a.id,
        resource_pool_id=pool_a.id,
        admin_username="admin",
        ttl_seconds=3600,
    )
    sess_a_id = enabled_a["id"]
    gen_a = enabled_a["generation"]

    # Admin initiates release via HTTP route → RELEASING, lease stays ACTIVE
    rel_resp = client.post(
        f"/api/v1/network-supply/sessions/{sess_a_id}/release",
        headers=admin_headers,
    )
    assert rel_resp.status_code == 200, rel_resp.text
    assert rel_resp.json()["data"]["status"] == "RELEASING"

    # Agent reports offline failure (confirmed=False)
    fail_resp = client.post(
        f"/api/v1/agent/network-sessions/{sess_a_id}/release-report",
        headers=agent_headers,
        json={
            "host_id": host_a.id,
            "generation": gen_a,
            "confirmed": False,
            "error_message": "adb offline",
        },
    )
    assert fail_resp.status_code == 200, fail_resp.text
    fail_data = fail_resp.json()["data"]
    assert fail_data["status"] == "RELEASE_FAILED"
    assert fail_data["lease_released"] is False
    assert fail_data["converted_to_debt"] is False
    assert fail_data["open_debt_count"] == 0

    # ZERO DeviceReturnDebt rows for wifi; manual_network_supply lease kept at SAME generation!
    db_session.expire_all()
    assert db_session.query(DeviceReturnDebt).filter_by(device_id=dev_a.id).count() == 0
    lease_a = (
        db_session.query(DeviceLease)
        .filter_by(device_id=dev_a.id, status=LeaseStatus.ACTIVE.value)
        .one()
    )
    assert lease_a.reason == MANUAL_NETWORK_LEASE_REASON
    assert lease_a.lease_generation == gen_a

    # Heartbeat delivers pending manual release to host
    pending = pending_manual_network_releases_for_host_sync(db_session, host_a.id)
    assert len(pending) == 1
    assert pending[0]["session_id"] == sess_a_id
    assert pending[0]["generation"] == gen_a
    assert pending[0]["status"] == "RELEASE_FAILED"

    # Device reconnects → Agent reports confirmed=True with same generation
    ok_resp = client.post(
        f"/api/v1/agent/network-sessions/{sess_a_id}/release-report",
        headers=agent_headers,
        json={"host_id": host_a.id, "generation": gen_a, "confirmed": True},
    )
    assert ok_resp.status_code == 200, ok_resp.text
    ok_data = ok_resp.json()["data"]
    assert ok_data["status"] == "RELEASED"
    assert ok_data["release_reason"] == "manual_released"
    assert ok_data["lease_released"] is True

    db_session.expire_all()
    assert db_session.query(DeviceReturnDebt).filter_by(device_id=dev_a.id).count() == 0
    assert (
        db_session.query(DeviceLease)
        .filter_by(device_id=dev_a.id, status=LeaseStatus.ACTIVE.value)
        .count()
    ) == 0

    # Path B: offline manual wifi session closed via dedicated admin audit-override
    host_b, dev_b, pool_b = _seed_host_device_pool(db_session, resource_type="wifi")
    db_session.commit()
    enabled_b = enable_manual_network_supply_sync(
        db_session,
        device_id=dev_b.id,
        resource_pool_id=pool_b.id,
        admin_username="admin",
        ttl_seconds=3600,
    )
    sess_b_id = enabled_b["id"]
    gen_b = enabled_b["generation"]
    report_manual_network_release_sync(
        db_session,
        session_id=sess_b_id,
        host_id=host_b.id,
        generation=gen_b,
        confirmed=False,
        error_message="device unplugged",
    )

    ov_resp = client.post(
        f"/api/v1/network-supply/sessions/{sess_b_id}/audit-override",
        headers=admin_headers,
        json={
            "reason_category": "device_unreachable",
            "evidence_ref": "OPS-4212-wifi-offline",
        },
    )
    assert ov_resp.status_code == 200, ov_resp.text
    ov_data = ov_resp.json()["data"]
    assert ov_data["session"]["status"] == "RELEASED"
    assert ov_data["session"]["release_reason"] == "manual_override"
    assert ov_data["lease_released"] is True
    assert ov_data["usbnet_revoke_directive"] is None

    db_session.expire_all()
    assert db_session.query(DeviceReturnDebt).filter_by(device_id=dev_b.id).count() == 0
    assert (
        db_session.query(DeviceLease)
        .filter_by(device_id=dev_b.id, status=LeaseStatus.ACTIVE.value)
        .count()
    ) == 0
    audit = (
        db_session.query(AuditLog)
        .filter_by(
            action="network_session_audit_override",
            resource_type="device_network_session",
            resource_id=str(sess_b_id),
        )
        .first()
    )
    assert audit is not None
    assert audit.details["evidence_ref"] == "OPS-4212-wifi-offline"


# ── Anti-case ⑤: Manual usb_net debt conversion, discharge(N+1), override + revoke ─


def test_u2_anticase5_manual_usbnet_debt_discharge_override_and_revoke(
    db_session, client, agent_headers, ed25519_grant_key,
):
    # Path A: Manual usb_net unconfirmed → N→N+1 debt lease → discharge(N+1) closes all
    host_a, dev_a, pool_a = _seed_host_device_pool(db_session, resource_type="usb_net")
    db_session.commit()
    enabled_a = enable_manual_network_supply_sync(
        db_session,
        device_id=dev_a.id,
        resource_pool_id=pool_a.id,
        admin_username="admin",
        ttl_seconds=3600,
    )
    sess_a_id = enabled_a["id"]
    gen_a = enabled_a["generation"]

    # Forged confirmed=True without dual-side evidence MUST fail with 422
    with pytest.raises(UnprocessableEntity):
        report_manual_network_release_sync(
            db_session,
            session_id=sess_a_id,
            host_id=host_a.id,
            generation=gen_a,
            confirmed=True,
            evidence={"mode": "disarm", "success": True, "readback": "clear", "kind": "ok"},
        )

    # Unconfirmed release → RELEASE_FAILED + open usb_net debt + N→N+1 debt lease
    fail_a = report_manual_network_release_sync(
        db_session,
        session_id=sess_a_id,
        host_id=host_a.id,
        generation=gen_a,
        confirmed=False,
        error_message="usb cable disconnected",
    )
    assert fail_a["status"] == "RELEASE_FAILED"
    assert fail_a["converted_to_debt"] is True
    assert fail_a["open_debt_count"] == 1
    debt_token_a = fail_a["fencing_token"]
    assert debt_token_a == f"{dev_a.id}:{gen_a + 1}"

    debt_row = (
        db_session.query(DeviceReturnDebt)
        .filter_by(device_id=dev_a.id, family="usb_net", state="open")
        .one()
    )
    assert debt_row.source_kind == MANUAL_NETWORK_RETURN_SOURCE_KIND
    assert debt_row.source_job_id is None
    assert debt_row.evidence["session_id"] == sess_a_id

    # Discharge with N+1 closes manual usb_net debt, session & debt lease atomically
    dis_a = apply_discharge_sync(
        db_session,
        device_id=dev_a.id,
        fencing_token=debt_token_a,
        family_results={"usb_net": _usbnet_confirmed_entry()},
    )
    db_session.commit()
    assert dis_a["ok"] is True
    assert dis_a["discharged_families"] == ["usb_net"]
    assert dis_a["debt_lease_released"] is True
    assert dis_a["open_debt_count"] == 0

    db_session.expire_all()
    sess_a = db_session.get(DeviceNetworkSession, sess_a_id)
    dev_a_ref = db_session.get(Device, dev_a.id)
    assert sess_a.status == "RELEASED"
    assert sess_a.release_reason == "debt_discharged"
    assert dev_a_ref.return_state["usb_net"]["state"] == "returned"

    # Path B: Manual usb_net debt closed via dedicated admin audit-override + usbnet-revoke
    host_b, dev_b, pool_b = _seed_host_device_pool(db_session, resource_type="usb_net")
    db_session.commit()
    enabled_b = enable_manual_network_supply_sync(
        db_session,
        device_id=dev_b.id,
        resource_pool_id=pool_b.id,
        admin_username="admin",
        ttl_seconds=3600,
    )
    sess_b_id = enabled_b["id"]
    gen_b = enabled_b["generation"]
    report_manual_network_release_sync(
        db_session,
        session_id=sess_b_id,
        host_id=host_b.id,
        generation=gen_b,
        confirmed=False,
        error_message="device hung in rndis",
    )

    ov_b = audit_override_network_session_sync(
        db_session,
        session_id=sess_b_id,
        reason_category="manual_recovery",
        evidence_ref="OPS-4212-usbnet-override",
        admin_username="admin",
    )
    assert ov_b["session"]["status"] == "RELEASED"
    assert ov_b["session"]["release_reason"] == "manual_override"
    assert ov_b["closed_debt_count"] == 1
    assert ov_b["lease_released"] is True
    assert ov_b["open_debt_count"] == 0
    assert ov_b["usbnet_revoke_directive"] == {
        "session_id": sess_b_id,
        "generation": gen_b,
        "device_serial": dev_b.serial,
        "host_id": host_b.id,
        "usb_devpath": "1-2.3",
    }

    db_session.expire_all()
    dev_b_ref = db_session.get(Device, dev_b.id)
    # Audit override MUST record unverified(override), NEVER returned!
    assert dev_b_ref.return_state["usb_net"]["state"] == "unverified"
    assert dev_b_ref.return_state["usb_net"]["source"] == "override"
    assert (
        db_session.query(DeviceLease)
        .filter_by(device_id=dev_b.id, status=LeaseStatus.ACTIVE.value)
        .count()
    ) == 0

    # Heartbeat exposes pending usbnet-revoke directive until Agent acknowledges
    revokes_before = pending_usbnet_revokes_for_host_sync(db_session, host_b.id)
    assert len(revokes_before) == 1
    assert revokes_before[0]["session_id"] == sess_b_id
    assert revokes_before[0]["generation"] == gen_b
    assert revokes_before[0]["device_serial"] == dev_b.serial

    ack_resp = client.post(
        f"/api/v1/agent/network-sessions/{sess_b_id}/revoke-ack",
        headers=agent_headers,
        json={"host_id": host_b.id, "generation": gen_b},
    )
    assert ack_resp.status_code == 200, ack_resp.text
    assert ack_resp.json()["data"]["acknowledged"] is True
    assert pending_usbnet_revokes_for_host_sync(db_session, host_b.id) == []


def test_u2_audit_override_preserves_debt_lease_when_other_family_has_open_debt(
    db_session, ed25519_grant_key,
):
    host, dev, pool = _seed_host_device_pool(db_session, resource_type="usb_net")
    db_session.commit()
    enabled = enable_manual_network_supply_sync(
        db_session,
        device_id=dev.id,
        resource_pool_id=pool.id,
        admin_username="admin",
        ttl_seconds=3600,
    )
    sess_id = enabled["id"]
    gen = enabled["generation"]
    # Simulate an independent open power-cycle debt on the same device
    db_session.add(
        DeviceReturnDebt(
            device_id=dev.id,
            family="power-cycle",
            source_kind="stock",
            source_job_id=None,
            cause="incomplete",
            state="open",
        )
    )
    db_session.commit()

    ov = audit_override_network_session_sync(
        db_session,
        session_id=sess_id,
        reason_category="ops_cleanup",
        evidence_ref="OPS-4212-coexisting",
        admin_username="admin",
    )
    assert ov["session"]["status"] == "RELEASED"
    assert ov["lease_released"] is False
    assert ov["lease_converted_to_debt"] is True
    assert ov["open_debt_count"] == 1

    db_session.expire_all()
    lease = (
        db_session.query(DeviceLease)
        .filter_by(device_id=dev.id, status=LeaseStatus.ACTIVE.value)
        .one()
    )
    assert lease.reason == DEBT_LEASE_REASON
    assert lease.lease_generation == gen + 1


def test_u2_reaper_transitions_expired_manual_session_to_releasing_without_releasing_lease(
    db_session,
):
    host, dev, pool = _seed_host_device_pool(db_session, resource_type="wifi")
    db_session.commit()
    enabled = enable_manual_network_supply_sync(
        db_session,
        device_id=dev.id,
        resource_pool_id=pool.id,
        admin_username="admin",
        ttl_seconds=600,
    )
    sess_id = enabled["id"]
    sess = db_session.get(DeviceNetworkSession, sess_id)
    sess.status = "ACTIVE"
    sess.expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    db_session.commit()

    stats = reap_expired_manual_network_sessions_sync(db_session)
    db_session.commit()
    assert stats["transitioned"] == 1

    db_session.expire_all()
    sess_after = db_session.get(DeviceNetworkSession, sess_id)
    lease_after = (
        db_session.query(DeviceLease)
        .filter_by(device_id=dev.id, status=LeaseStatus.ACTIVE.value)
        .one()
    )
    assert sess_after.status == "RELEASING"
    assert sess_after.released_at is None
    assert lease_after.reason == MANUAL_NETWORK_LEASE_REASON
    assert lease_after.lease_generation == enabled["generation"]
