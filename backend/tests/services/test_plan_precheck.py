"""ADR-0021 C3 — Dispatch gate (precheck) integration tests."""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from backend.models.enums import HostStatus, JobStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.models.script import Script
from backend.services.plan_dispatcher_sync import prepare_plan_run
from backend.services.plan_dispatcher_core import PlanDispatchError
from backend.services.plan_precheck import _drive_dispatch_gate
from backend.tasks.saq_worker import EnqueueSyncError


# ---------------------------------------------------------------------------
# Fixtures: Plan + Script + Host + Device chain ready for dispatch
# (gate_chain lives in backend/tests/conftest.py)
# ---------------------------------------------------------------------------


def _ack_ok(host_id: str, expected_sha: str) -> dict:
    return {
        "host_id": host_id,
        "agent_version": "test",
        "results": [
            {
                "name": "check_device",
                "version": "1.0.0",
                "expected_sha": expected_sha,
                "actual_sha": expected_sha,
                "exists": True,
                "ok": True,
                "error": None,
            }
        ],
        "checked_at": "2026-05-07T10:00:00Z",
    }


def _ack_drift(host_id: str, expected_sha: str) -> dict:
    return {
        "host_id": host_id,
        "agent_version": "test",
        "results": [
            {
                "name": "check_device",
                "version": "1.0.0",
                "expected_sha": expected_sha,
                "actual_sha": "deadbeef",
                "exists": True,
                "ok": False,
                "error": None,
            }
        ],
        "checked_at": "2026-05-07T10:00:00Z",
    }


def _prepare_run(db_session, gate_chain) -> PlanRun:
    pr = prepare_plan_run(
        plan_id=gate_chain["plan"].id,
        device_ids=[gate_chain["device_a"].id, gate_chain["device_b"].id],
        triggered_by="testuser",
        db=db_session,
        run_type="MANUAL",
    )
    return pr


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


class TestDispatchGate:
    def test_no_drift_dispatches_jobs(self, db_session, gate_chain):
        pr = _prepare_run(db_session, gate_chain)

        async def _fake_call(host_id, event, data, *, timeout=10.0):
            return _ack_ok(host_id, "aabbcc11")

        with patch(
            "backend.services.plan_precheck.call_agent_rpc",
            side_effect=_fake_call,
        ):
            asyncio.run(_drive_dispatch_gate(pr.id, db=db_session))

        db_session.expire_all()
        pr_after = db_session.get(PlanRun, pr.id)
        assert pr_after.status == "RUNNING"
        precheck = pr_after.run_context["precheck"]
        assert precheck["phase"] == "ready"
        assert precheck["final_result"] == "ready"
        assert precheck["completed_at"] is not None
        assert precheck["hosts"]["h-A"]["status"] == "ok"
        assert precheck["hosts"]["h-B"]["status"] == "ok"

        jobs = db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id
        ).all()
        assert len(jobs) == 2
        assert {j.host_id for j in jobs} == {"h-A", "h-B"}
        assert all(j.status == JobStatus.PENDING.value for j in jobs)

    def test_one_host_drift_resync_and_dispatch(self, db_session, gate_chain):
        pr = _prepare_run(db_session, gate_chain)

        verify_call = {"count": 0}

        async def _fake_call(host_id, event, data, *, timeout=10.0):
            verify_call["count"] += 1
            if host_id == "h-A":
                return _ack_ok(host_id, "aabbcc11")
            # h-B drifts on first verify, aligns on re-verify
            if verify_call["count"] <= 2:
                return _ack_drift(host_id, "aabbcc11")
            return _ack_ok(host_id, "aabbcc11")

        with patch(
            "backend.services.plan_precheck.call_agent_rpc",
            side_effect=_fake_call,
        ), patch(
            "backend.services.plan_precheck._sync_host_via_hot_update",
            return_value=(True, None),
        ), patch(
            "backend.services.plan_precheck.SYNC_SETTLE_SECONDS", 0
        ):
            asyncio.run(_drive_dispatch_gate(pr.id, db=db_session))

        db_session.expire_all()
        pr_after = db_session.get(PlanRun, pr.id)
        precheck = pr_after.run_context["precheck"]
        assert precheck["final_result"] == "ready"
        assert precheck["hosts"]["h-B"]["status"] == "ok"
        assert precheck["hosts"]["h-B"]["sync_attempts"] == 1
        assert precheck["hosts"]["h-B"]["synced_at"] is not None
        assert pr_after.status == "RUNNING"

        jobs = db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id
        ).count()
        assert jobs == 2

    def test_agent_offline_terminal_failure(self, db_session, gate_chain):
        pr = _prepare_run(db_session, gate_chain)

        from backend.realtime.socketio_server import AgentNotConnectedError

        async def _fake_call(host_id, event, data, *, timeout=10.0):
            if host_id == "h-A":
                return _ack_ok(host_id, "aabbcc11")
            raise AgentNotConnectedError(host_id)

        sync_called = {"count": 0}

        def _fake_sync(host_id, db):
            sync_called["count"] += 1
            return (True, None)

        with patch(
            "backend.services.plan_precheck.call_agent_rpc",
            side_effect=_fake_call,
        ), patch(
            "backend.services.plan_precheck._sync_host_via_hot_update",
            side_effect=_fake_sync,
        ):
            asyncio.run(_drive_dispatch_gate(pr.id, db=db_session))

        db_session.expire_all()
        pr_after = db_session.get(PlanRun, pr.id)
        assert pr_after.status == "FAILED"
        assert pr_after.result_summary["precheck_failed"] is True
        assert "agent_offline" in pr_after.result_summary["reason"]

        precheck = pr_after.run_context["precheck"]
        assert precheck["phase"] == "failed"
        assert precheck["final_result"] == "failed"
        assert precheck["hosts"]["h-B"]["error"] == "agent_offline"
        # offline detected before sync — sync must NOT have been triggered.
        assert sync_called["count"] == 0

        jobs = db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id
        ).count()
        assert jobs == 0

    def test_sync_failed_marks_plan_run_failed(self, db_session, gate_chain):
        pr = _prepare_run(db_session, gate_chain)

        async def _fake_call(host_id, event, data, *, timeout=10.0):
            return _ack_drift(host_id, "aabbcc11")  # both drift

        def _fake_sync_fail(host_id, db):
            return (False, "no_ssh_credentials")

        with patch(
            "backend.services.plan_precheck.call_agent_rpc",
            side_effect=_fake_call,
        ), patch(
            "backend.services.plan_precheck._sync_host_via_hot_update",
            side_effect=_fake_sync_fail,
        ):
            asyncio.run(_drive_dispatch_gate(pr.id, db=db_session))

        db_session.expire_all()
        pr_after = db_session.get(PlanRun, pr.id)
        assert pr_after.status == "FAILED"
        assert "sync_failed" in pr_after.result_summary["reason"]

        precheck = pr_after.run_context["precheck"]
        assert precheck["final_result"] == "failed"
        assert precheck["hosts"]["h-A"]["error"] == "no_ssh_credentials"
        assert precheck["hosts"]["h-B"]["error"] == "no_ssh_credentials"

    def test_reverify_still_failing_marks_plan_run_failed(
        self, db_session, gate_chain
    ):
        pr = _prepare_run(db_session, gate_chain)

        async def _fake_call(host_id, event, data, *, timeout=10.0):
            # always returns drift — re-verify after sync still bad
            return _ack_drift(host_id, "aabbcc11")

        with patch(
            "backend.services.plan_precheck.call_agent_rpc",
            side_effect=_fake_call,
        ), patch(
            "backend.services.plan_precheck._sync_host_via_hot_update",
            return_value=(True, None),
        ), patch(
            "backend.services.plan_precheck.SYNC_SETTLE_SECONDS", 0
        ):
            asyncio.run(_drive_dispatch_gate(pr.id, db=db_session))

        db_session.expire_all()
        pr_after = db_session.get(PlanRun, pr.id)
        assert pr_after.status == "FAILED"
        assert "reverify_failed" in pr_after.result_summary["reason"]
        precheck = pr_after.run_context["precheck"]
        assert precheck["final_result"] == "failed"

    def test_skip_when_plan_run_not_running(self, db_session, gate_chain):
        pr = _prepare_run(db_session, gate_chain)
        pr.status = "FAILED"
        db_session.commit()

        async def _fake_call(host_id, event, data, *, timeout=10.0):
            pytest.fail("call_agent_rpc must not be invoked when status != RUNNING")

        with patch(
            "backend.services.plan_precheck.call_agent_rpc",
            side_effect=_fake_call,
        ):
            asyncio.run(_drive_dispatch_gate(pr.id, db=db_session))

    def test_no_scripts_in_snapshot_marks_failed(self, db_session, gate_chain):
        pr = _prepare_run(db_session, gate_chain)

        # Wipe scripts so _expected_scripts_for_run returns []
        db_session.query(Script).delete()
        db_session.commit()

        with patch(
            "backend.services.plan_precheck.call_agent_rpc",
            new_callable=AsyncMock,
        ) as mock_call:
            asyncio.run(_drive_dispatch_gate(pr.id, db=db_session))
            mock_call.assert_not_called()

        db_session.expire_all()
        pr_after = db_session.get(PlanRun, pr.id)
        assert pr_after.status == "FAILED"
        assert "no_scripts_resolved" in pr_after.result_summary["reason"]


# ---------------------------------------------------------------------------
# /plans/{id}/run endpoint integration: enqueues without dispatching
# ---------------------------------------------------------------------------


class TestRunPlanEndpointEnqueues:
    def test_run_plan_creates_plan_run_and_enqueues(
        self, client, auth_headers, db_session, gate_chain
    ):
        plan_id = gate_chain["plan"].id
        device_ids = [gate_chain["device_a"].id]

        with patch("backend.api.routes.plans.enqueue_sync") as enq:
            resp = client.post(
                f"/api/v1/plans/{plan_id}/run",
                json={"device_ids": device_ids},
                headers=auth_headers,
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["status"] == "RUNNING"
        assert data["run_type"] == "MANUAL"
        assert data["run_context"]["dispatch_device_ids"] == device_ids
        # No JobInstances yet — gate hasn't run.
        jobs = db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == data["id"]
        ).count()
        assert jobs == 0

        enq.assert_called_once()
        kwargs = enq.call_args.kwargs
        assert enq.call_args.args[0] == "precheck_and_dispatch_task"
        assert enq.call_args.kwargs["required"] is True
        assert kwargs["plan_run_id"] == data["id"]
        assert kwargs["key"] == f"precheck:{data['id']}"

    def test_run_plan_returns_503_when_enqueue_unavailable(
        self, client, auth_headers, db_session, gate_chain,
    ):
        plan_id = gate_chain["plan"].id
        device_ids = [gate_chain["device_a"].id]

        with patch(
            "backend.api.routes.plans.enqueue_sync",
            side_effect=EnqueueSyncError("SAQ not running"),
        ):
            resp = client.post(
                f"/api/v1/plans/{plan_id}/run",
                json={"device_ids": device_ids},
                headers=auth_headers,
            )

        assert resp.status_code == 503, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "DISPATCH_QUEUE_UNAVAILABLE"
        plan_run_id = detail["plan_run_id"]
        pr = db_session.get(PlanRun, plan_run_id)
        assert pr.status == "FAILED"
        assert pr.result_summary["reason"] == "dispatch_queue_unavailable"


# ---------------------------------------------------------------------------
# dispatch_state structure (ADR-0021 audit)
# ---------------------------------------------------------------------------


class TestDispatchStatePersistence:
    def test_run_plan_records_dispatch_state(
        self, client, auth_headers, db_session, gate_chain
    ):
        plan_id = gate_chain["plan"].id
        device_ids = [gate_chain["device_a"].id]

        with patch("backend.api.routes.plans.enqueue_sync"):
            resp = client.post(
                f"/api/v1/plans/{plan_id}/run",
                json={"device_ids": device_ids},
                headers=auth_headers,
            )

        assert resp.status_code == 200, resp.text
        plan_run_id = resp.json()["data"]["id"]

        pr = db_session.get(PlanRun, plan_run_id)
        assert pr is not None

        state = pr.run_context["dispatch_state"]
        assert state["enqueue_key"] == f"precheck:{plan_run_id}"
        assert state["requeue_attempts"] == 0
        assert state["status"] == "queued"
        assert state["enqueued_at"] is not None
        datetime.strptime(state["enqueued_at"], "%Y-%m-%dT%H:%M:%S.%fZ")
        assert state["started_at"] is None
        assert state["completed_at"] is None
        assert state["last_error"] is None

        # dispatch_device_ids must still be present
        assert pr.run_context["dispatch_device_ids"] == device_ids

    def test_gate_updates_dispatch_state_on_run(
        self, db_session, gate_chain
    ):
        pr = _prepare_run(db_session, gate_chain)
        # Simulate dispatch_state being seeded (as API would do)
        run_ctx = dict(pr.run_context or {})
        run_ctx["dispatch_state"] = {
            "enqueue_key": f"precheck:{pr.id}",
            "requeue_attempts": 0,
            "status": "queued",
            "enqueued_at": "2026-05-10T07:00:00.000Z",
            "started_at": None,
            "completed_at": None,
            "last_error": None,
        }
        pr.run_context = run_ctx
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(pr, "run_context")
        db_session.commit()

        async def _fake_call(host_id, event, data, *, timeout=10.0):
            return _ack_ok(host_id, "aabbcc11")

        with patch(
            "backend.services.plan_precheck.call_agent_rpc",
            side_effect=_fake_call,
        ):
            asyncio.run(_drive_dispatch_gate(pr.id, db=db_session))

        db_session.expire_all()
        pr_after = db_session.get(PlanRun, pr.id)
        state = pr_after.run_context["dispatch_state"]
        assert state["status"] == "completed"
        assert state["started_at"] is not None
        assert state["completed_at"] is not None
        assert state["last_error"] is None


# ---------------------------------------------------------------------------
# ADR-0023 C1 — Gate coordinates with complete-time fail-fast
# ---------------------------------------------------------------------------


class TestDispatchGateCoordinatesCompleteFailure:
    """precheck Phase 4 后,若 complete_plan_run_dispatch 内部写 FAILED
    (脚本在 prepare 到 complete 之间被失活),gate 必须重读 status 并把
    dispatch_state 收口为 ``failed``,而不是按默认 ready 路径写 ``completed``。"""

    def test_complete_failed_overrides_default_dispatch_state(
        self, db_session, gate_chain
    ):
        from sqlalchemy.orm.attributes import flag_modified
        from backend.models.plan_run import PlanRun

        pr = _prepare_run(db_session, gate_chain)
        run_ctx = dict(pr.run_context or {})
        run_ctx["dispatch_state"] = {
            "enqueue_key": f"precheck:{pr.id}",
            "requeue_attempts": 0,
            "status": "queued",
            "enqueued_at": "2026-05-10T07:00:00.000Z",
            "started_at": None,
            "completed_at": None,
            "last_error": None,
        }
        pr.run_context = run_ctx
        flag_modified(pr, "run_context")
        db_session.commit()

        async def _fake_call(host_id, event, data, *, timeout=10.0):
            return _ack_ok(host_id, "aabbcc11")

        def _fake_complete(plan_run_id, db):
            # 模拟 ADR-0023 C1 阶段 2:complete 内部捕获 keys 缺失,
            # 写 FAILED + result_summary,但不抛异常。
            from datetime import datetime, timezone as _tz
            row = db.get(PlanRun, plan_run_id)
            row.status = "FAILED"
            row.ended_at = datetime.now(_tz.utc)
            row.result_summary = {
                "dispatch_failed": True,
                "missing_scripts": ["check_device:1.0.0"],
            }
            flag_modified(row, "result_summary")
            db.commit()

        with patch(
            "backend.services.plan_precheck.call_agent_rpc",
            side_effect=_fake_call,
        ), patch(
            "backend.services.plan_precheck.complete_plan_run_dispatch",
            side_effect=_fake_complete,
        ):
            asyncio.run(_drive_dispatch_gate(pr.id, db=db_session))

        db_session.expire_all()
        pr_after = db_session.get(PlanRun, pr.id)
        # PlanRun 已是 FAILED,result_summary 完整保留
        assert pr_after.status == "FAILED"
        assert pr_after.result_summary == {
            "dispatch_failed": True,
            "missing_scripts": ["check_device:1.0.0"],
        }
        # dispatch_state.status 必须是 "failed",last_error 携带 missing keys
        state = pr_after.run_context["dispatch_state"]
        assert state["status"] == "failed"
        assert state["completed_at"] is not None
        assert state["last_error"] is not None
        assert "check_device:1.0.0" in state["last_error"]
        # Job 不应该被创建
        from backend.models.job import JobInstance
        assert db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id
        ).count() == 0


# ---------------------------------------------------------------------------
# SocketIO invalidation hints (P0-2 precheck observability)
# ---------------------------------------------------------------------------


class TestPrecheckSocketBroadcast:
    def test_persist_precheck_emits_invalidation(self, db_session, gate_chain):
        from backend.services.plan_precheck import _persist_precheck

        pr = _prepare_run(db_session, gate_chain)
        captured: list[tuple] = []

        def fake_schedule_emit(event, data, namespace="/dashboard", room=None):
            captured.append((event, data, namespace, room))

        precheck = {
            "phase": "verifying",
            "started_at": "2026-05-07T10:00:00Z",
            "completed_at": None,
            "hosts": {},
            "errors": [],
        }

        with patch(
            "backend.realtime.socketio_server.schedule_emit",
            side_effect=fake_schedule_emit,
        ):
            _persist_precheck(pr.id, precheck, db_session)

        assert len(captured) == 1
        event, data, namespace, room = captured[0]
        assert event == "precheck_update"
        assert data["type"] == "PRECHECK_UPDATE"
        assert data["payload"]["phase"] == "verifying"
        assert namespace == "/dashboard"
        assert room == f"plan_run:{pr.id}"

    def test_drive_dispatch_gate_emits_precheck_updates(self, db_session, gate_chain):
        pr = _prepare_run(db_session, gate_chain)
        captured: list[tuple] = []

        def fake_schedule_emit(event, data, namespace="/dashboard", room=None):
            captured.append((event, data, namespace, room))

        async def _fake_call(host_id, event, data, *, timeout=10.0):
            return _ack_ok(host_id, "aabbcc11")

        with patch(
            "backend.realtime.socketio_server.schedule_emit",
            side_effect=fake_schedule_emit,
        ), patch(
            "backend.services.plan_precheck.call_agent_rpc",
            side_effect=_fake_call,
        ):
            asyncio.run(_drive_dispatch_gate(pr.id, db=db_session))

        precheck_events = [
            item for item in captured if item[0] == "precheck_update"
        ]
        assert len(precheck_events) >= 2
        phases = {
            item[1]["payload"].get("phase")
            for item in precheck_events
            if item[1]["payload"].get("phase")
        }
        assert "verifying" in phases
        assert "ready" in phases
        rooms = {item[3] for item in precheck_events}
        assert rooms == {f"plan_run:{pr.id}"}

    def test_unexpected_exception_emits_precheck_update(self, db_session, gate_chain):
        pr = _prepare_run(db_session, gate_chain)
        captured: list[tuple] = []

        def fake_schedule_emit(event, data, namespace="/dashboard", room=None):
            captured.append((event, data, namespace, room))

        with patch(
            "backend.realtime.socketio_server.schedule_emit",
            side_effect=fake_schedule_emit,
        ), patch(
            "backend.services.plan_precheck._gather_verify",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                asyncio.run(_drive_dispatch_gate(pr.id, db=db_session))

        failed_events = [
            item for item in captured if item[0] == "precheck_update"
        ]
        assert len(failed_events) >= 1
        assert failed_events[-1][1]["payload"]["phase"] == "failed"
        assert failed_events[-1][1]["payload"]["dispatch_status"] == "failed"
        assert failed_events[-1][3] == f"plan_run:{pr.id}"


# ---------------------------------------------------------------------------
# CHAIN / SCHEDULE gate routing (P1 — no bypass)
# ---------------------------------------------------------------------------


class TestChainScheduleDispatchGate:
    def test_dispatch_plan_sync_runs_precheck_gate(self, db_session, gate_chain):
        from backend.services.plan_dispatcher_sync import dispatch_plan_sync

        async def _fake_call(host_id, event, data, *, timeout=10.0):
            return _ack_ok(host_id, "aabbcc11")

        with patch(
            "backend.services.plan_precheck.call_agent_rpc",
            side_effect=_fake_call,
        ):
            pr = dispatch_plan_sync(
                plan_id=gate_chain["plan"].id,
                device_ids=[gate_chain["device_a"].id],
                triggered_by="chain-test",
                db=db_session,
                run_type="CHAIN",
                run_context={"triggered_from_plan_run_id": 99},
            )

        assert pr.run_type == "CHAIN"
        assert pr.run_context["precheck"]["phase"] == "ready"
        assert pr.run_context["dispatch_state"]["status"] == "completed"
        assert (
            db_session.query(JobInstance)
            .filter(JobInstance.plan_run_id == pr.id)
            .count()
            == 1
        )

    def test_dispatch_plan_sync_gate_failure_raises(self, db_session, gate_chain):
        from backend.realtime.socketio_server import AgentNotConnectedError
        from backend.services.plan_dispatcher_sync import dispatch_plan_sync

        async def _fake_call(host_id, event, data, *, timeout=10.0):
            raise AgentNotConnectedError(host_id)

        with patch(
            "backend.services.plan_precheck.call_agent_rpc",
            side_effect=_fake_call,
        ), pytest.raises(PlanDispatchError, match="dispatch gate failed"):
            dispatch_plan_sync(
                plan_id=gate_chain["plan"].id,
                device_ids=[gate_chain["device_a"].id],
                triggered_by="schedule-test",
                db=db_session,
                run_type="SCHEDULE",
            )
