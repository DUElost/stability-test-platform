"""ADR-0022 — POST /api/v1/agent/jobs/{id}/patrol-heartbeat tests.

Validates:
  - happy path: counters incremented, current_step / current_failure_streak /
    next_retry_at written, last_patrol_heartbeat_at touched
  - cycle_index uses GREATEST() so out-of-order heartbeats don't regress
  - manual_action_observed atomically clears the column
  - 404 for unknown job; 409 for invalid fencing_token; 400 for bad payload
  - the endpoint does NOT write step_trace

Note (project convention):
  This file follows the test_agent_api_watcher pattern — it directly invokes
  async route handlers via SessionLocal / AsyncSessionLocal so seed data is
  visible to the async DB session.  Only runs when TEST_DATABASE_URL points at
  PostgreSQL (SQLite quick-test path skips because the two engines do not share
  in-memory storage).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("DATABASE_URL", "").startswith("sqlite"),
    reason="agent API contract tests need PostgreSQL (cross-engine seed); "
           "SQLite quick-test mode skips automatically.",
)

from backend.api.routes.agent_api import (
    PatrolHeartbeatIn,
    patrol_heartbeat,
)
from backend.core.database import AsyncSessionLocal, SessionLocal
from backend.models.device_lease import DeviceLease
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.job import JobInstance, StepTrace
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun


# ---------------------------------------------------------------------------
# Seed helpers — write via sync SessionLocal so async route can read them
# ---------------------------------------------------------------------------


def _seed_patrol_chain(*, job_status: str = JobStatus.RUNNING.value) -> dict:
    suffix = uuid4().hex[:8]
    host_id = f"patrol-host-{suffix}"
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        host = Host(
            id=host_id,
            hostname=f"ph-{suffix}",
            status=HostStatus.ONLINE.value,
            created_at=now,
        )
        device = Device(
            serial=f"PSN-{suffix}",
            host_id=host_id,
            status="BUSY",
            tags=[],
            created_at=now,
        )
        plan = Plan(
            name=f"patrol-plan-{suffix}",
            failure_threshold=0.05,
            created_by="pytest",
        )
        db.add_all([host, device, plan])
        db.flush()

        pr = PlanRun(
            plan_id=plan.id,
            status="RUNNING",
            failure_threshold=0.05,
            plan_snapshot={"plan": {"id": plan.id}, "steps": []},
            run_type="MANUAL",
            triggered_by="pytest",
        )
        db.add(pr)
        db.flush()

        job = JobInstance(
            plan_run_id=pr.id,
            plan_id=plan.id,
            device_id=device.id,
            host_id=host_id,
            status=job_status,
            pipeline_def={"lifecycle": {"init": [], "patrol": {"steps": []}, "teardown": []}},
            started_at=now if job_status == JobStatus.RUNNING.value else None,
        )
        db.add(job)
        db.flush()

        token = f"patrol-tok-{suffix}"
        lease = DeviceLease(
            device_id=device.id,
            job_id=job.id,
            host_id=host_id,
            lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=token,
            lease_generation=1,
            agent_instance_id="pytest-agent",
            acquired_at=now,
            renewed_at=now,
            expires_at=now + timedelta(seconds=600),
        )
        db.add(lease)
        db.commit()

        return {
            "host_id": host_id,
            "device_id": device.id,
            "plan_id": plan.id,
            "plan_run_id": pr.id,
            "job_id": job.id,
            "token": token,
        }
    finally:
        db.close()


def _cleanup_patrol_chain(seed: dict) -> None:
    db = SessionLocal()
    try:
        db.query(StepTrace).filter(StepTrace.job_id == seed["job_id"]).delete()
        db.query(DeviceLease).filter(DeviceLease.job_id == seed["job_id"]).delete()
        db.query(JobInstance).filter(JobInstance.id == seed["job_id"]).delete()
        db.query(PlanRun).filter(PlanRun.id == seed["plan_run_id"]).delete()
        db.query(Plan).filter(Plan.id == seed["plan_id"]).delete()
        db.query(Device).filter(Device.id == seed["device_id"]).delete()
        db.query(Host).filter(Host.id == seed["host_id"]).delete()
        db.commit()
    finally:
        db.close()


async def _call_heartbeat(job_id: int, payload: PatrolHeartbeatIn):
    async with AsyncSessionLocal() as db:
        return await patrol_heartbeat(job_id=job_id, payload=payload, db=db)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestPatrolHeartbeatHappyPath:
    @pytest.mark.asyncio
    async def test_first_heartbeat_writes_all_fields(self):
        seed = _seed_patrol_chain()
        try:
            resp = await _call_heartbeat(
                seed["job_id"],
                PatrolHeartbeatIn(
                    fencing_token=seed["token"],
                    cycle_index=1,
                    success_delta=1,
                    failed_delta=0,
                    current_step="patrol.monkey_check",
                    current_failure_streak=0,
                    next_retry_at=None,
                ),
            )
            data = resp.data
            assert data.job_id == seed["job_id"]
            assert data.patrol_cycle_count == 1
            assert data.patrol_success_cycle_count == 1
            assert data.patrol_failed_cycle_count == 0
            assert data.current_failure_streak == 0
            assert data.next_retry_at is None
            assert data.manual_action is None

            db = SessionLocal()
            try:
                refreshed = db.get(JobInstance, seed["job_id"])
                assert refreshed.patrol_cycle_count == 1
                assert refreshed.patrol_success_cycle_count == 1
                assert refreshed.current_patrol_step == "patrol.monkey_check"
                assert refreshed.last_patrol_heartbeat_at is not None
            finally:
                db.close()
        finally:
            _cleanup_patrol_chain(seed)

    @pytest.mark.asyncio
    async def test_cumulative_deltas_across_cycles(self):
        seed = _seed_patrol_chain()
        try:
            for i in range(1, 6):
                had_failure = i % 3 == 0
                await _call_heartbeat(
                    seed["job_id"],
                    PatrolHeartbeatIn(
                        fencing_token=seed["token"],
                        cycle_index=i,
                        success_delta=0 if had_failure else 1,
                        failed_delta=1 if had_failure else 0,
                        current_failure_streak=1 if had_failure else 0,
                    ),
                )

            db = SessionLocal()
            try:
                refreshed = db.get(JobInstance, seed["job_id"])
                assert refreshed.patrol_cycle_count == 5
                # i ∈ {3} fails out of 1..5 → 1 failed, 4 success
                assert refreshed.patrol_success_cycle_count == 4
                assert refreshed.patrol_failed_cycle_count == 1
            finally:
                db.close()
        finally:
            _cleanup_patrol_chain(seed)

    @pytest.mark.asyncio
    async def test_cycle_index_greatest_wins_out_of_order(self):
        seed = _seed_patrol_chain()
        try:
            await _call_heartbeat(
                seed["job_id"],
                PatrolHeartbeatIn(
                    fencing_token=seed["token"], cycle_index=7, success_delta=1,
                ),
            )
            await _call_heartbeat(
                seed["job_id"],
                PatrolHeartbeatIn(
                    fencing_token=seed["token"], cycle_index=3, success_delta=1,
                ),
            )

            db = SessionLocal()
            try:
                refreshed = db.get(JobInstance, seed["job_id"])
                assert refreshed.patrol_cycle_count == 7  # GREATEST(7, 3)
                assert refreshed.patrol_success_cycle_count == 2  # both deltas applied
            finally:
                db.close()
        finally:
            _cleanup_patrol_chain(seed)

    @pytest.mark.asyncio
    async def test_endpoint_does_not_write_step_trace(self):
        seed = _seed_patrol_chain()
        try:
            db = SessionLocal()
            try:
                baseline = db.query(StepTrace).filter(
                    StepTrace.job_id == seed["job_id"]
                ).count()
            finally:
                db.close()

            for i in range(1, 11):
                await _call_heartbeat(
                    seed["job_id"],
                    PatrolHeartbeatIn(
                        fencing_token=seed["token"],
                        cycle_index=i,
                        success_delta=1,
                    ),
                )

            db = SessionLocal()
            try:
                after = db.query(StepTrace).filter(
                    StepTrace.job_id == seed["job_id"]
                ).count()
                assert after == baseline, "patrol-heartbeat must NOT write step_trace"
            finally:
                db.close()
        finally:
            _cleanup_patrol_chain(seed)


# ---------------------------------------------------------------------------
# manual_action observation
# ---------------------------------------------------------------------------


class TestManualActionObservation:
    @pytest.mark.asyncio
    async def test_manual_action_observed_clears_column(self):
        seed = _seed_patrol_chain()
        try:
            # Pre-set manual_action via direct DB write
            db = SessionLocal()
            try:
                job = db.get(JobInstance, seed["job_id"])
                job.manual_action = "RETRY_NOW"
                db.commit()
            finally:
                db.close()

            # Heartbeat without observed → server keeps the action pending
            r1 = await _call_heartbeat(
                seed["job_id"],
                PatrolHeartbeatIn(fencing_token=seed["token"], cycle_index=1),
            )
            assert r1.data.manual_action == "RETRY_NOW"

            # Heartbeat WITH observed=RETRY_NOW → server clears
            r2 = await _call_heartbeat(
                seed["job_id"],
                PatrolHeartbeatIn(
                    fencing_token=seed["token"],
                    cycle_index=2,
                    manual_action_observed="RETRY_NOW",
                ),
            )
            assert r2.data.manual_action is None

            db = SessionLocal()
            try:
                assert db.get(JobInstance, seed["job_id"]).manual_action is None
            finally:
                db.close()
        finally:
            _cleanup_patrol_chain(seed)

    @pytest.mark.asyncio
    async def test_manual_action_observed_does_not_clear_when_db_switched(self):
        """TOCTOU race 防御:Agent 报 observed=RETRY_NOW,但 DB 已被用户切到
        EXIT_REQUESTED;heartbeat 端点必须用 SQL CASE 条件清除 — DB 值不匹配
        observed 时原样保留,防止用户新意图被静默吞掉。
        """
        seed = _seed_patrol_chain()
        try:
            # 模拟: Agent 看到的是 RETRY_NOW,但在 heartbeat 抵达前用户切到 EXIT_REQUESTED
            db = SessionLocal()
            try:
                job = db.get(JobInstance, seed["job_id"])
                job.manual_action = "EXIT_REQUESTED"
                db.commit()
            finally:
                db.close()

            resp = await _call_heartbeat(
                seed["job_id"],
                PatrolHeartbeatIn(
                    fencing_token=seed["token"],
                    cycle_index=1,
                    manual_action_observed="RETRY_NOW",  # 旧意图
                ),
            )
            # 返回的 manual_action 应是 EXIT_REQUESTED — 不被静默清除
            assert resp.data.manual_action == "EXIT_REQUESTED"

            db = SessionLocal()
            try:
                assert (
                    db.get(JobInstance, seed["job_id"]).manual_action
                    == "EXIT_REQUESTED"
                )
            finally:
                db.close()
        finally:
            _cleanup_patrol_chain(seed)

    @pytest.mark.asyncio
    async def test_manual_action_observed_clears_only_matching_value(self):
        """对称回归:observed=EXIT_REQUESTED + DB=EXIT_REQUESTED → 清除;
        observed=EXIT_REQUESTED + DB=RETRY_NOW → 保留。"""
        seed = _seed_patrol_chain()
        try:
            # 正常匹配清除
            db = SessionLocal()
            try:
                db.get(JobInstance, seed["job_id"]).manual_action = "EXIT_REQUESTED"
                db.commit()
            finally:
                db.close()

            r1 = await _call_heartbeat(
                seed["job_id"],
                PatrolHeartbeatIn(
                    fencing_token=seed["token"],
                    cycle_index=1,
                    manual_action_observed="EXIT_REQUESTED",
                ),
            )
            assert r1.data.manual_action is None

            # DB 切换到 RETRY_NOW,Agent 仍报旧的 EXIT_REQUESTED → 不清
            db = SessionLocal()
            try:
                db.get(JobInstance, seed["job_id"]).manual_action = "RETRY_NOW"
                db.commit()
            finally:
                db.close()

            r2 = await _call_heartbeat(
                seed["job_id"],
                PatrolHeartbeatIn(
                    fencing_token=seed["token"],
                    cycle_index=2,
                    manual_action_observed="EXIT_REQUESTED",
                ),
            )
            assert r2.data.manual_action == "RETRY_NOW"
        finally:
            _cleanup_patrol_chain(seed)


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestPatrolHeartbeatErrors:
    @pytest.mark.asyncio
    async def test_unknown_job_returns_404(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await _call_heartbeat(
                999_999_999,
                PatrolHeartbeatIn(fencing_token="x", cycle_index=1),
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_invalid_fencing_token_returns_409(self):
        from fastapi import HTTPException
        seed = _seed_patrol_chain()
        try:
            with pytest.raises(HTTPException) as exc:
                await _call_heartbeat(
                    seed["job_id"],
                    PatrolHeartbeatIn(fencing_token="wrong", cycle_index=1),
                )
            assert exc.value.status_code == 409
        finally:
            _cleanup_patrol_chain(seed)

    @pytest.mark.asyncio
    async def test_negative_delta_returns_400(self):
        from fastapi import HTTPException
        seed = _seed_patrol_chain()
        try:
            with pytest.raises(HTTPException) as exc:
                await _call_heartbeat(
                    seed["job_id"],
                    PatrolHeartbeatIn(
                        fencing_token=seed["token"],
                        cycle_index=1,
                        success_delta=-1,
                    ),
                )
            assert exc.value.status_code == 400
        finally:
            _cleanup_patrol_chain(seed)

    @pytest.mark.asyncio
    async def test_negative_cycle_index_returns_400(self):
        from fastapi import HTTPException
        seed = _seed_patrol_chain()
        try:
            with pytest.raises(HTTPException) as exc:
                await _call_heartbeat(
                    seed["job_id"],
                    PatrolHeartbeatIn(fencing_token=seed["token"], cycle_index=-1),
                )
            assert exc.value.status_code == 400
        finally:
            _cleanup_patrol_chain(seed)

    @pytest.mark.asyncio
    async def test_invalid_next_retry_at_returns_400(self):
        from fastapi import HTTPException
        seed = _seed_patrol_chain()
        try:
            with pytest.raises(HTTPException) as exc:
                await _call_heartbeat(
                    seed["job_id"],
                    PatrolHeartbeatIn(
                        fencing_token=seed["token"],
                        cycle_index=1,
                        next_retry_at="not-a-datetime",
                    ),
                )
            assert exc.value.status_code == 400
        finally:
            _cleanup_patrol_chain(seed)


# ---------------------------------------------------------------------------
# ADR-0022 D10 — JOB_NOT_RUNNING contract (双侧 CAS)
# ---------------------------------------------------------------------------


class TestPatrolHeartbeatStallContract:
    @pytest.mark.asyncio
    async def test_patrol_heartbeat_returns_409_when_job_not_running(self):
        """改动 A — Job status 在请求进入时已非 RUNNING(典型: recycler 已 CAS 推到 UNKNOWN)
        → helper 之前直接 409 JOB_NOT_RUNNING;DB 上 last_patrol_heartbeat_at 不被更新。"""
        from fastapi import HTTPException
        from sqlalchemy import update as sa_update

        seed = _seed_patrol_chain()
        try:
            db = SessionLocal()
            try:
                db.execute(
                    sa_update(JobInstance)
                    .where(JobInstance.id == seed["job_id"])
                    .values(status=JobStatus.UNKNOWN.value, last_patrol_heartbeat_at=None)
                )
                db.commit()
            finally:
                db.close()

            with pytest.raises(HTTPException) as exc:
                await _call_heartbeat(
                    seed["job_id"],
                    PatrolHeartbeatIn(
                        fencing_token=seed["token"],
                        cycle_index=1,
                        success_delta=1,
                    ),
                )
            assert exc.value.status_code == 409
            assert isinstance(exc.value.detail, dict)
            assert exc.value.detail.get("code") == "JOB_NOT_RUNNING"
            assert "recovery/sync" in exc.value.detail.get("message", "")

            db = SessionLocal()
            try:
                refreshed = db.get(JobInstance, seed["job_id"])
                assert refreshed.last_patrol_heartbeat_at is None, (
                    "改动 A 必须在 helper 之前拦截,heartbeat 写入路径不应执行"
                )
                assert refreshed.patrol_cycle_count == 0
            finally:
                db.close()
        finally:
            _cleanup_patrol_chain(seed)

    @pytest.mark.asyncio
    async def test_patrol_heartbeat_succeeds_when_status_running(self):
        """改动 B regression — CAS 添加 status='RUNNING' guard 不破坏正常 happy path。"""
        seed = _seed_patrol_chain()
        try:
            resp = await _call_heartbeat(
                seed["job_id"],
                PatrolHeartbeatIn(
                    fencing_token=seed["token"],
                    cycle_index=1,
                    success_delta=1,
                ),
            )
            assert resp.data.patrol_cycle_count == 1

            db = SessionLocal()
            try:
                refreshed = db.get(JobInstance, seed["job_id"])
                assert refreshed.status == JobStatus.RUNNING.value
                assert refreshed.last_patrol_heartbeat_at is not None
                assert refreshed.patrol_cycle_count == 1
            finally:
                db.close()
        finally:
            _cleanup_patrol_chain(seed)

    @pytest.mark.asyncio
    async def test_patrol_heartbeat_cas_returns_409_when_status_races_after_precheck(
        self, monkeypatch
    ):
        """改动 B — 罕见 race:改动 A 通过(快照看到 RUNNING),helper 在执行期间
        recycler 把行 flip 到 UNKNOWN,然后 CAS WHERE status='RUNNING' 失配 → 0 行 → 409。

        通过 monkeypatch _require_valid_runtime_lease 让它在通过前用单独的同步 session
        把真实 DB 行的 status flip 到 UNKNOWN,精确模拟「预校验后、CAS 前」的 race 窗口。
        """
        from fastapi import HTTPException
        from sqlalchemy import update as sa_update
        from backend.api.routes import agent_api as agent_api_mod

        seed = _seed_patrol_chain()
        try:
            async def _race_flip_then_pass(db, job, fencing_token):
                """模拟 race:在 CAS 之前另一会话把行 flip。"""
                sync = SessionLocal()
                try:
                    sync.execute(
                        sa_update(JobInstance)
                        .where(JobInstance.id == seed["job_id"])
                        .values(status=JobStatus.UNKNOWN.value)
                    )
                    sync.commit()
                finally:
                    sync.close()
                return None

            monkeypatch.setattr(
                agent_api_mod,
                "_require_valid_runtime_lease",
                _race_flip_then_pass,
            )

            with pytest.raises(HTTPException) as exc:
                await _call_heartbeat(
                    seed["job_id"],
                    PatrolHeartbeatIn(
                        fencing_token=seed["token"],
                        cycle_index=1,
                        success_delta=1,
                    ),
                )
            assert exc.value.status_code == 409
            assert isinstance(exc.value.detail, dict)
            assert exc.value.detail.get("code") == "JOB_NOT_RUNNING"
            assert "flipped" in exc.value.detail.get("message", "")

            db = SessionLocal()
            try:
                refreshed = db.get(JobInstance, seed["job_id"])
                assert refreshed.status == JobStatus.UNKNOWN.value
                assert refreshed.patrol_cycle_count == 0, (
                    "CAS 失配后必须 rollback,counter 不应被写入"
                )
            finally:
                db.close()
        finally:
            _cleanup_patrol_chain(seed)
