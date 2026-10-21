"""#3723 §3.6.7（v1.7.10，U5）：欠账转换后的 /complete 重放识别。

背景：U3 把 JOB 租约**原地**转换成欠账租约时清空了租约的 job_id，历史 JOB
租约行随之不可查；`agent_completion` 的已终态分支因此把「载荷完全相同的合法
重放」当成过期令牌拒绝（409 STALE_COMPLETION_TOKEN），并记 stale 审计。

§3.6.7 第 1 条：历史 JOB 租约行不存在、且重放载荷摘要与
`job_instance.terminal_payload_digest` 一致时，按只读重放返回 200，不改任何
事实、不记 stale 审计；摘要不一致时沿用现行规则（先迟到 confirmed，否则 409）。

本文件用真实 `/api/v1/agent/jobs/{id}/complete` 入口（不是 helper 直调）覆盖：
同载荷重放 200 且无新增审计、不同载荷仍 409、迟到 confirmed 仍然生效。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend.models.audit import AuditLog
from backend.core.database import SessionLocal
from backend.models.device_lease import DeviceLease
from backend.models.device_return_debt import DeviceReturnDebt
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun

_NOW = datetime.now(timezone.utc)


def _seed_job(db, *, disarm_result: str = "failed"):
    """建一个 RUNNING job + ACTIVE JOB 租约（令牌与后续 /complete 一致）。"""
    suffix = uuid4().hex[:8]
    host = Host(
        id=f"u5-replay-{suffix}",
        hostname=f"u5-replay-{suffix}",
        status=HostStatus.ONLINE.value,
        last_heartbeat=_NOW,
        created_at=_NOW,
    )
    device = Device(
        serial=f"U5R-{suffix}",
        host_id=host.id,
        status="ONLINE",
        tags=[],
        created_at=_NOW,
        last_seen=_NOW,
        lease_generation=1,
    )
    plan = Plan(name=f"u5-replay-{suffix}", description="u5 replay", created_by="test")
    db.add_all([host, device, plan])
    db.flush()
    run = PlanRun(
        plan_id=plan.id,
        status="RUNNING",
        plan_snapshot={"name": plan.name},
        run_type="MANUAL",
        triggered_by="test",
    )
    db.add(run)
    db.flush()
    job = JobInstance(
        plan_run_id=run.id,
        plan_id=plan.id,
        device_id=device.id,
        host_id=host.id,
        status=JobStatus.RUNNING.value,
        pipeline_def={
            "lifecycle": {
                "init": [{"step_id": "setup", "action": "script:powercycle_setup"}],
                "teardown": [{"step_id": "finish", "action": "script:powercycle_finish"}],
            }
        },
        created_at=_NOW,
        updated_at=_NOW,
        started_at=_NOW,
    )
    db.add(job)
    db.flush()
    token = f"{device.id}:1"
    lease = DeviceLease(
        device_id=device.id,
        job_id=job.id,
        host_id=host.id,
        lease_type=LeaseType.JOB.value,
        status=LeaseStatus.ACTIVE.value,
        fencing_token=token,
        lease_generation=1,
        agent_instance_id=f"agent-{suffix}",
        acquired_at=_NOW,
        renewed_at=_NOW,
        expires_at=_NOW + timedelta(hours=1),
    )
    db.add(lease)
    db.commit()
    db.refresh(job)
    db.refresh(lease)
    return job, lease, token, device.id


def _payload(token: str, *, result: str = "failed", status: str = "FAILED"):
    return {
        "update": {"status": status},
        "fencing_token": token,
        "disarm_results": {"power-cycle": {"result": result, "step_id": "finish"}},
    }


def _audit_actions(job_id: int) -> list[str]:
    with SessionLocal() as db:
        return [
            row.action
            for row in db.query(AuditLog)
            .filter(AuditLog.resource_type == "job_instance")
            .filter(AuditLog.resource_id == str(job_id))
            .all()
        ]


def _debt_rows(device_id: int):
    with SessionLocal() as db:
        return [
            (row.family, row.state, row.source_job_id)
            for row in db.query(DeviceReturnDebt)
            .filter(DeviceReturnDebt.device_id == device_id)
            .all()
        ]


def test_replay_same_payload_after_debt_conversion_is_read_only_200(client, db_session):
    """§3.6.7 第 1、3 条：已接入专项失败并转换后，同载荷重放 200 且不新增审计。"""
    job, lease, token, device_id = _seed_job(db_session)

    first = client.post(f"/api/v1/agent/jobs/{job.id}/complete", json=_payload(token))
    assert first.status_code == 200, first.text
    # U3：失败结果建欠账，JOB 租约原地转换成欠账租约（job_id 被清空）
    with SessionLocal() as db:
        row = db.get(DeviceLease, lease.id)
        assert row.lease_type == LeaseType.MAINTENANCE.value, row.lease_type
        assert row.job_id is None, row.job_id
    assert _debt_rows(device_id) == [("power-cycle", "open", job.id)], _debt_rows(device_id)

    audits_before = _audit_actions(job.id)
    replay = client.post(f"/api/v1/agent/jobs/{job.id}/complete", json=_payload(token))
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["idempotent"] is True, replay.text

    # 不改任何事实：欠账仍在、租约仍是欠账租约、job 事实不变
    assert _debt_rows(device_id) == [("power-cycle", "open", job.id)], _debt_rows(device_id)
    with SessionLocal() as db:
        row = db.get(DeviceLease, lease.id)
        assert row.lease_type == LeaseType.MAINTENANCE.value
        assert row.job_id is None
    # 不记 stale 审计，也不记终态载荷冲突审计
    audits_after = _audit_actions(job.id)
    assert audits_after == audits_before, (audits_before, audits_after)
    assert "stale_job_completion_rejected" not in audits_after
    assert "terminal_payload_conflict" not in audits_after


def test_replay_different_payload_after_debt_conversion_still_409(client, db_session):
    """§3.6.7 第 1 条后半：摘要不一致时沿用现行规则（无迟到 confirmed → 409）。"""
    job, lease, token, device_id = _seed_job(db_session)
    first = client.post(f"/api/v1/agent/jobs/{job.id}/complete", json=_payload(token))
    assert first.status_code == 200, first.text

    other = client.post(
        f"/api/v1/agent/jobs/{job.id}/complete",
        json=_payload(token, result="executed_unverified", status="ABORTED"),
    )
    assert other.status_code == 409, other.text
    assert other.json()["detail"]["code"] == "STALE_COMPLETION_TOKEN", other.text


def test_late_confirmed_still_closes_same_source_after_change(client, db_session):
    """迟到 confirmed 现行回归保持通过：同源令牌仍能关掉欠账。"""
    job, lease, token, device_id = _seed_job(db_session)
    first = client.post(f"/api/v1/agent/jobs/{job.id}/complete", json=_payload(token))
    assert first.status_code == 200, first.text
    assert _debt_rows(device_id) == [("power-cycle", "open", job.id)], _debt_rows(device_id)

    late = client.post(
        f"/api/v1/agent/jobs/{job.id}/complete",
        json=_payload(token, result="confirmed"),
    )
    assert late.status_code == 200, late.text
    assert late.json()["data"].get("late_confirmed"), late.text
    assert _debt_rows(device_id) == [("power-cycle", "closed", job.id)], _debt_rows(device_id)


def test_wrong_token_replay_after_debt_conversion_changes_nothing(client, db_session):
    """欠账转换后令牌不符的重放：即便按摘要放行也不得改事实、不得记 stale 审计。

    §3.6.7 第 1 条：历史 JOB 租约行不存在时，识别重放的信号是「已接受终态载荷
    的摘要」——原令牌可能只保存在同源欠账条目的 source_fencing_token 里，也可能
    因「其它来源欠账触发转换」而无处可查。摘要不含令牌，故令牌不符但载荷相同的
    重放同样按只读重放处理；其安全性在于不改任何事实、不记 stale 审计。
    """
    job, lease, token, device_id = _seed_job(db_session)
    first = client.post(f"/api/v1/agent/jobs/{job.id}/complete", json=_payload(token))
    assert first.status_code == 200, first.text
    audits_before = _audit_actions(job.id)

    wrong = client.post(
        f"/api/v1/agent/jobs/{job.id}/complete",
        json=_payload("not-the-original-token"),
    )
    assert wrong.status_code == 200, wrong.text
    assert wrong.json()["data"]["idempotent"] is True, wrong.text
    # 事实未变：欠账仍开着、租约仍是欠账租约、审计未新增
    assert _debt_rows(device_id) == [("power-cycle", "open", job.id)], _debt_rows(device_id)
    with SessionLocal() as db:
        row = db.get(DeviceLease, lease.id)
        assert row.lease_type == LeaseType.MAINTENANCE.value
        assert row.job_id is None
    audits_after = _audit_actions(job.id)
    assert audits_after == audits_before, (audits_before, audits_after)
    assert "stale_job_completion_rejected" not in audits_after


def test_wrong_token_replay_with_historical_lease_still_409(client, db_session):
    """历史 JOB 租约行仍在、令牌不符 → 仍 409（现行规则不变）。"""
    job, lease, token, device_id = _seed_job(db_session)
    # executed_unverified 不建欠账 → JOB 租约被释放但行仍在（lease_type 仍为 JOB）
    first = client.post(
        f"/api/v1/agent/jobs/{job.id}/complete",
        json=_payload(token, result="executed_unverified"),
    )
    assert first.status_code == 200, first.text
    with SessionLocal() as db:
        row = db.get(DeviceLease, lease.id)
        assert row.lease_type == LeaseType.JOB.value, row.lease_type
        assert row.job_id == job.id, row.job_id

    wrong = client.post(
        f"/api/v1/agent/jobs/{job.id}/complete",
        json=_payload("not-the-original-token", result="executed_unverified"),
    )
    assert wrong.status_code == 409, wrong.text
    assert wrong.json()["detail"]["code"] == "STALE_COMPLETION_TOKEN", wrong.text

