"""ADR-0021 C4 — PlanRun abort API + host hot-update soft-lock tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from backend.api.routes.agent_api import _RunCompleteIn, complete_job
from backend.core.database import AsyncSessionLocal, async_engine
from backend.models.device_lease import DeviceLease
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.models.script import Script
from backend.services.plan_run_abort import abort_plan_run


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def abort_chain(db_session):
    host = Host(
        id="h-abort", hostname="hostX",
        status=HostStatus.ONLINE.value,
        ip="10.0.0.50",
        ssh_user="root",
        ssh_port=22,
        extra={"ssh_password": "x"},
        last_heartbeat=datetime.now(timezone.utc),
    )
    dev1 = Device(serial="dev-1", host_id="h-abort", status="BUSY")
    dev2 = Device(serial="dev-2", host_id="h-abort", status="ONLINE")
    script = Script(
        name="check_device", script_type="python", version="1.0.0",
        nfs_path="/scripts/check_device/v1.0.0/check_device.py",
        content_sha256="abc", default_params={"timeout": 30},
    )
    plan = Plan(name="abort-plan", patrol_interval_seconds=60)
    db_session.add_all([host, dev1, dev2, script, plan])
    db_session.commit()
    db_session.add(PlanStep(
        plan_id=plan.id, step_key="init_check",
        script_name="check_device", script_version="1.0.0",
        stage="init", sort_order=0, timeout_seconds=30, retry=0,
    ))
    db_session.commit()
    return {"host": host, "dev1": dev1, "dev2": dev2, "plan": plan}


def _make_plan_run(
    db_session, plan_id: int, *, run_context: dict | None = None,
    status: str = "RUNNING",
) -> PlanRun:
    pr = PlanRun(
        plan_id=plan_id,
        status=status,
        failure_threshold=0.05,
        plan_snapshot={"plan": {"id": plan_id}, "steps": []},
        run_type="MANUAL",
        run_context=run_context,
        triggered_by="testuser",
        chain_index=0,
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(pr)
    db_session.commit()
    db_session.refresh(pr)
    return pr


def _make_job(
    db_session, plan_run_id: int, plan_id: int, device_id: int, host_id: str,
    status: str,
) -> JobInstance:
    job = JobInstance(
        plan_run_id=plan_run_id,
        plan_id=plan_id,
        device_id=device_id,
        host_id=host_id,
        status=status,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _make_hidden_legacy_plan(db_session) -> Plan:
    plan = Plan(
        name="hidden-legacy-abort-plan",
        description="legacy aee plan kept only for compatibility",
        failure_threshold=0.05,
        created_by="testuser",
    )
    db_session.add(plan)
    db_session.flush()
    db_session.add_all([
        PlanStep(
            plan_id=plan.id,
            step_key="init_0",
            script_name="check_device",
            script_version="1.0.0",
            stage="init",
            sort_order=0,
            timeout_seconds=30,
            retry=0,
        ),
        PlanStep(
            plan_id=plan.id,
            step_key="scan",
            script_name="scan_aee",
            script_version="1.0.0",
            stage="patrol",
            sort_order=1,
            timeout_seconds=30,
            retry=0,
        ),
    ])
    db_session.commit()
    db_session.refresh(plan)
    return plan


def _make_active_lease(db_session, device_id: int, host_id: str, job_id: int) -> DeviceLease:
    now = datetime.now(timezone.utc)
    lease = DeviceLease(
        device_id=device_id,
        job_id=job_id,
        host_id=host_id,
        lease_type=LeaseType.JOB.value,
        status=LeaseStatus.ACTIVE.value,
        fencing_token=f"{device_id}:1",
        lease_generation=1,
        agent_instance_id="test-agent",
        acquired_at=now,
        renewed_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    db_session.add(lease)
    db_session.commit()
    db_session.refresh(lease)
    return lease


# ---------------------------------------------------------------------------
# POST /plan-runs/{id}/abort
# ---------------------------------------------------------------------------


class TestPlanRunAbort:
    def test_abort_running_plan_run_keeps_lease_and_aborts_pending(
        self, client, auth_headers, db_session, abort_chain
    ):
        plan = abort_chain["plan"]
        pr = _make_plan_run(
            db_session, plan.id,
            run_context={"precheck": {"phase": "ready", "started_at": "x", "hosts": {}}},
        )
        running_job = _make_job(
            db_session, pr.id, plan.id,
            abort_chain["dev1"].id, "h-abort",
            status=JobStatus.RUNNING.value,
        )
        pending_job = _make_job(
            db_session, pr.id, plan.id,
            abort_chain["dev2"].id, "h-abort",
            status=JobStatus.PENDING.value,
        )
        lease = _make_active_lease(
            db_session, abort_chain["dev1"].id, "h-abort", running_job.id
        )

        resp = client.post(
            f"/api/v1/plan-runs/{pr.id}/abort",
            json={"reason": "误派发"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["plan_run_id"] == pr.id
        assert body["phase"] == "running"
        assert body["aborted_jobs"] == [pending_job.id]
        assert body["abort_requested_jobs"] == [running_job.id]
        assert body["released_leases"] == 0

        db_session.expire_all()
        # PENDING → ABORTED inline; RUNNING stays RUNNING (Agent will drain).
        assert db_session.get(JobInstance, pending_job.id).status == JobStatus.ABORTED.value
        assert db_session.get(JobInstance, running_job.id).status == JobStatus.RUNNING.value

        # RUNNING 的 lease 必须保持 ACTIVE，直到 Agent ACK ABORTED。
        assert db_session.get(DeviceLease, lease.id).status == LeaseStatus.ACTIVE.value

        # PlanRun.run_context.abort_requested set.
        pr_after = db_session.get(PlanRun, pr.id)
        assert pr_after.run_context["abort_requested"]["reason"] == "误派发"

    @pytest.mark.asyncio
    async def test_abort_running_job_releases_lease_only_after_agent_ack(
        self, db_session, abort_chain,
    ):
        plan = abort_chain["plan"]
        pr = _make_plan_run(db_session, plan.id)
        running_job = _make_job(
            db_session,
            pr.id,
            plan.id,
            abort_chain["dev1"].id,
            "h-abort",
            status=JobStatus.RUNNING.value,
        )
        lease = _make_active_lease(
            db_session, abort_chain["dev1"].id, "h-abort", running_job.id,
        )

        with patch("backend.services.plan_run_abort.schedule_emit"), patch(
            "backend.services.plan_run_abort.should_trigger_dedup",
            return_value=False,
        ):
            summary = abort_plan_run(
                pr.id,
                db=db_session,
                reason="用户停止",
                triggered_by="testuser",
            )

        assert summary["abort_requested_jobs"] == [running_job.id]
        db_session.expire_all()
        assert db_session.get(JobInstance, running_job.id).status == JobStatus.RUNNING.value
        assert db_session.get(DeviceLease, lease.id).status == LeaseStatus.ACTIVE.value

        await async_engine.dispose()
        with patch(
            "backend.api.routes.agent_api.broadcast_run_job_update",
            new=AsyncMock(),
        ), patch(
            "backend.api.routes.agent_api.broadcast_plan_run_status",
            new=AsyncMock(),
        ), patch(
            "backend.tasks.saq_worker.get_queue",
        ) as get_queue:
            get_queue.return_value.enqueue = AsyncMock()
            async with AsyncSessionLocal() as async_db:
                result = await complete_job(
                    job_id=running_job.id,
                    payload=_RunCompleteIn(
                        update={"status": "ABORTED", "exit_code": 130},
                        fencing_token=lease.fencing_token,
                    ),
                    db=async_db,
                    _=None,
                )

        assert result.error is None
        assert result.data["status"] == JobStatus.ABORTED.value
        db_session.expire_all()
        assert db_session.get(JobInstance, running_job.id).status == JobStatus.ABORTED.value
        assert db_session.get(DeviceLease, lease.id).status == LeaseStatus.RELEASED.value
        persisted_run = db_session.get(PlanRun, pr.id)
        assert persisted_run.status == "FAILED"
        assert persisted_run.result_summary["abort_requested"] is True
        assert persisted_run.result_summary["aborted"] == 1

    def test_abort_during_precheck_marks_plan_run_failed_and_aborted(
        self, client, auth_headers, db_session, abort_chain
    ):
        plan = abort_chain["plan"]
        pr = _make_plan_run(
            db_session, plan.id,
            run_context={
                "precheck": {
                    "phase": "verifying",
                    "started_at": "x",
                    "hosts": {"h-abort": {"status": "pending", "scripts": []}},
                    "errors": [],
                },
                "dispatch_device_ids": [abort_chain["dev1"].id],
            },
        )
        # No JobInstance created — gate hasn't dispatched yet.

        resp = client.post(
            f"/api/v1/plan-runs/{pr.id}/abort",
            json={"reason": "用户取消"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["phase"] == "precheck"
        assert body["aborted_jobs"] == []
        assert body["released_leases"] == 0

        db_session.expire_all()
        pr_after = db_session.get(PlanRun, pr.id)
        assert pr_after.status == "FAILED"
        assert pr_after.result_summary["aborted"] is True
        assert pr_after.result_summary["reason"] == "用户取消"
        assert pr_after.run_context["precheck"]["final_result"] == "aborted"
        assert pr_after.run_context["precheck"]["phase"] == "failed"

    def test_abort_running_plan_run_without_jobs_marks_run_failed(
        self, client, auth_headers, db_session, abort_chain
    ):
        plan = abort_chain["plan"]
        pr = _make_plan_run(
            db_session,
            plan.id,
            run_context={"dispatch_device_ids": [abort_chain["dev1"].id]},
        )

        resp = client.post(
            f"/api/v1/plan-runs/{pr.id}/abort",
            json={"reason": "用户取消"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["phase"] == "running"
        assert body["aborted_jobs"] == []
        assert body["released_leases"] == 0
        assert body["status"] == "FAILED"

        db_session.expire_all()
        pr_after = db_session.get(PlanRun, pr.id)
        assert pr_after.status == "FAILED"
        assert pr_after.ended_at is not None
        assert pr_after.result_summary["aborted"] is True
        assert pr_after.result_summary["reason"] == "用户取消"
        assert pr_after.run_context["abort_requested"]["reason"] == "用户取消"

    def test_abort_running_plan_run_with_only_pending_jobs_marks_run_failed(
        self, client, auth_headers, db_session, abort_chain
    ):
        plan = abort_chain["plan"]
        pr = _make_plan_run(
            db_session,
            plan.id,
            run_context={"dispatch_device_ids": [abort_chain["dev1"].id]},
        )
        pending_job = _make_job(
            db_session,
            pr.id,
            plan.id,
            abort_chain["dev1"].id,
            "h-abort",
            status=JobStatus.PENDING.value,
        )

        resp = client.post(
            f"/api/v1/plan-runs/{pr.id}/abort",
            json={"reason": "用户取消"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["status"] == "FAILED"
        assert body["aborted_jobs"] == [pending_job.id]

        db_session.expire_all()
        pr_after = db_session.get(PlanRun, pr.id)
        assert pr_after.status == "FAILED"
        assert pr_after.ended_at is not None
        assert db_session.get(JobInstance, pending_job.id).status == JobStatus.ABORTED.value

    def test_abort_terminal_plan_run_returns_409(
        self, client, auth_headers, db_session, abort_chain
    ):
        pr = _make_plan_run(db_session, abort_chain["plan"].id, status="SUCCESS")
        resp = client.post(
            f"/api/v1/plan-runs/{pr.id}/abort", headers=auth_headers,
        )
        assert resp.status_code == 409
        assert "terminal" in resp.json()["detail"].lower()

    def test_abort_unknown_plan_run_returns_404(self, client, auth_headers):
        resp = client.post(
            "/api/v1/plan-runs/999999/abort", headers=auth_headers,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /hosts/{id} active_jobs surface
# ---------------------------------------------------------------------------


class TestHostActiveJobs:
    def test_get_host_includes_active_jobs(
        self, client, db_session, abort_chain, auth_headers
    ):
        plan = abort_chain["plan"]
        pr = _make_plan_run(db_session, plan.id)
        running_job = _make_job(
            db_session, pr.id, plan.id,
            abort_chain["dev1"].id, "h-abort",
            status=JobStatus.RUNNING.value,
        )

        resp = client.get("/api/v1/hosts/h-abort", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["active_job_count"] == 1
        assert len(body["active_jobs"]) == 1
        assert body["active_jobs"][0]["id"] == running_job.id
        assert body["active_jobs"][0]["plan_run_id"] == pr.id
        assert body["active_jobs"][0]["status"] == JobStatus.RUNNING.value

    def test_get_host_masks_hidden_legacy_plan_id_in_active_jobs(
        self, client, db_session, abort_chain, auth_headers
    ):
        hidden_plan = _make_hidden_legacy_plan(db_session)
        pr = _make_plan_run(db_session, hidden_plan.id)
        running_job = _make_job(
            db_session, pr.id, hidden_plan.id,
            abort_chain["dev1"].id, "h-abort",
            status=JobStatus.RUNNING.value,
        )

        resp = client.get("/api/v1/hosts/h-abort", headers=auth_headers)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["active_job_count"] == 1
        assert len(body["active_jobs"]) == 1
        assert body["active_jobs"][0]["id"] == running_job.id
        assert body["active_jobs"][0]["plan_run_id"] == pr.id
        assert body["active_jobs"][0]["plan_id"] is None
        assert body["active_jobs"][0]["status"] == JobStatus.RUNNING.value


# ---------------------------------------------------------------------------
# POST /hosts/{id}/hot-update soft-lock
# ---------------------------------------------------------------------------


class TestHostHotUpdateSoftLock:
    def test_hot_update_no_active_jobs_proceeds(
        self, client, admin_headers, db_session, abort_chain
    ):
        with patch(
            "backend.api.routes.hosts.execute_hot_update",
            return_value={"ok": True, "message": "ok", "duration_ms": 100},
        ):
            resp = client.post(
                "/api/v1/hosts/h-abort/hot-update", headers=admin_headers,
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["abort_summary"] is None

    def test_hot_update_with_sync_agent_secret_passes_secret_to_executor(
        self, client, admin_headers, db_session, abort_chain, monkeypatch
    ):
        monkeypatch.setenv("AGENT_SECRET", "sync-secret-1234567890")

        with patch(
            "backend.api.routes.hosts.execute_hot_update",
            return_value={"ok": True, "message": "ok", "duration_ms": 100},
        ) as mock_exec:
            resp = client.post(
                "/api/v1/hosts/h-abort/hot-update?sync_agent_secret=true",
                headers=admin_headers,
            )

        assert resp.status_code == 200, resp.text
        _, kwargs = mock_exec.call_args
        assert kwargs["sync_agent_secret"] is True
        assert kwargs["agent_secret"] == "sync-secret-1234567890"

    def test_hot_update_with_sync_agent_secret_rejects_placeholder_secret(
        self, client, admin_headers, db_session, abort_chain, monkeypatch
    ):
        monkeypatch.setenv("AGENT_SECRET", "change-me-in-production")

        with patch("backend.api.routes.hosts.execute_hot_update") as mock_exec:
            resp = client.post(
                "/api/v1/hosts/h-abort/hot-update?sync_agent_secret=true",
                headers=admin_headers,
            )

        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"] == (
            "Local AGENT_SECRET is not configured or still using a placeholder value."
        )
        mock_exec.assert_not_called()

    def test_hot_update_with_active_jobs_default_returns_409(
        self, client, admin_headers, db_session, abort_chain
    ):
        plan = abort_chain["plan"]
        pr = _make_plan_run(db_session, plan.id)
        _make_job(
            db_session, pr.id, plan.id,
            abort_chain["dev1"].id, "h-abort",
            status=JobStatus.RUNNING.value,
        )

        with patch("backend.api.routes.hosts.execute_hot_update") as mock_exec:
            resp = client.post(
                "/api/v1/hosts/h-abort/hot-update", headers=admin_headers,
            )
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["detail"]["code"] == "HOST_HAS_ACTIVE_JOBS"
        assert len(body["detail"]["active_jobs"]) == 1
        mock_exec.assert_not_called()

    def test_hot_update_masks_hidden_legacy_plan_id_in_active_job_summary(
        self, client, admin_headers, db_session, abort_chain
    ):
        hidden_plan = _make_hidden_legacy_plan(db_session)
        pr = _make_plan_run(db_session, hidden_plan.id)
        _make_job(
            db_session, pr.id, hidden_plan.id,
            abort_chain["dev1"].id, "h-abort",
            status=JobStatus.RUNNING.value,
        )

        with patch("backend.api.routes.hosts.execute_hot_update") as mock_exec:
            resp = client.post(
                "/api/v1/hosts/h-abort/hot-update", headers=admin_headers,
            )

        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["detail"]["code"] == "HOST_HAS_ACTIVE_JOBS"
        assert len(body["detail"]["active_jobs"]) == 1
        assert body["detail"]["active_jobs"][0]["plan_id"] is None
        mock_exec.assert_not_called()

    def test_hot_update_with_abort_running_jobs_drains_and_proceeds(
        self, client, admin_headers, db_session, abort_chain
    ):
        plan = abort_chain["plan"]
        pr = _make_plan_run(db_session, plan.id)
        pending_job = _make_job(
            db_session, pr.id, plan.id,
            abort_chain["dev1"].id, "h-abort",
            status=JobStatus.PENDING.value,
        )

        # PENDING jobs are aborted inline by abort_jobs_for_host →
        # _wait_until_no_active_jobs returns immediately.
        with patch(
            "backend.api.routes.hosts.execute_hot_update",
            return_value={"ok": True, "message": "drained+updated", "duration_ms": 50},
        ) as mock_exec:
            resp = client.post(
                "/api/v1/hosts/h-abort/hot-update?abort_running_jobs=true",
                headers=admin_headers,
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["abort_summary"] is not None
        assert pending_job.id in body["abort_summary"]["aborted_jobs"]
        mock_exec.assert_called_once()

        db_session.expire_all()
        assert (
            db_session.get(JobInstance, pending_job.id).status
            == JobStatus.ABORTED.value
        )

    def test_hot_update_drain_timeout_returns_504(
        self, client, admin_headers, db_session, abort_chain, monkeypatch
    ):
        plan = abort_chain["plan"]
        pr = _make_plan_run(db_session, plan.id)
        running_job = _make_job(
            db_session, pr.id, plan.id,
            abort_chain["dev1"].id, "h-abort",
            status=JobStatus.RUNNING.value,
        )
        # RUNNING jobs aren't transitioned inline by abort_jobs_for_host
        # (Agent must drain).  Configure the poll to time out quickly.
        monkeypatch.setattr(
            "backend.api.routes.hosts.HOT_UPDATE_ABORT_POLL_TIMEOUT_SECONDS", 0.05
        )
        monkeypatch.setattr(
            "backend.api.routes.hosts.HOT_UPDATE_ABORT_POLL_INTERVAL_SECONDS", 0.01
        )

        with patch("backend.api.routes.hosts.execute_hot_update") as mock_exec:
            resp = client.post(
                "/api/v1/hosts/h-abort/hot-update?abort_running_jobs=true",
                headers=admin_headers,
            )
        assert resp.status_code == 504, resp.text
        body = resp.json()
        assert body["detail"]["code"] == "ABORT_DRAIN_TIMEOUT"
        assert running_job.id in body["detail"]["lingering_jobs"]
        mock_exec.assert_not_called()


def test_abort_control_emit_scoped_per_host(db_session, abort_chain):
    """Each agent room must only receive abort job_ids bound to that host."""
    plan = abort_chain["plan"]
    host_b = Host(
        id="h-abort-b",
        hostname="hostB",
        status=HostStatus.ONLINE.value,
        ip="10.0.0.51",
        ssh_user="root",
        ssh_port=22,
        extra={"ssh_password": "x"},
        last_heartbeat=datetime.now(timezone.utc),
    )
    dev_b = Device(serial="dev-b", host_id="h-abort-b", status="BUSY")
    db_session.add_all([host_b, dev_b])
    db_session.commit()

    pr = _make_plan_run(db_session, plan.id)
    job_a = _make_job(
        db_session, pr.id, plan.id,
        abort_chain["dev1"].id, "h-abort",
        status=JobStatus.RUNNING.value,
    )
    job_b = _make_job(
        db_session, pr.id, plan.id,
        dev_b.id, "h-abort-b",
        status=JobStatus.RUNNING.value,
    )

    emitted: dict[str, list[int]] = {}

    def _capture_emit(_event, payload, *, namespace, room):
        if payload.get("command") == "abort":
            emitted[room] = list(payload["payload"]["job_ids"])

    with patch(
        "backend.services.plan_run_abort.schedule_emit",
        side_effect=_capture_emit,
    ), patch(
        "backend.services.plan_run_abort.should_trigger_dedup",
        return_value=False,
    ):
        abort_plan_run(pr.id, db=db_session, reason="scope-test")

    assert emitted["agent:h-abort"] == [job_a.id]
    assert emitted["agent:h-abort-b"] == [job_b.id]
    assert job_a.id not in emitted["agent:h-abort-b"]
    assert job_b.id not in emitted["agent:h-abort"]
