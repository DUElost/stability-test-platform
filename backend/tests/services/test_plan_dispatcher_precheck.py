"""Dispatch gate tests focused on single-device happy path + idempotency metadata."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from backend.models.enums import HostStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.models.script import Script
from backend.services.plan_dispatcher_sync import prepare_plan_run
from backend.services.plan_precheck import _drive_dispatch_gate
from backend.services.precheck.idempotency import (
    compute_dispatch_payload_hash,
    compute_idempotency_key,
)


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


@pytest.fixture
def single_device_gate_chain(db_session):
    host = Host(id="h-1", hostname="agent1", status=HostStatus.ONLINE.value, ip="10.0.0.9")
    device = Device(serial="dev-1", host_id="h-1", status="ONLINE")
    script = Script(
        name="check_device",
        script_type="python",
        version="1.0.0",
        nfs_path="/scripts/check_device/v1.0.0/check_device.py",
        content_sha256="aabbcc11",
        default_params={"timeout": 30},
    )
    plan = Plan(name="single-device-plan", patrol_interval_seconds=60)
    db_session.add_all([host, device, script, plan])
    db_session.commit()
    db_session.add(
        PlanStep(
            plan_id=plan.id,
            step_key="init_check",
            script_name="check_device",
            script_version="1.0.0",
            stage="init",
            sort_order=0,
            timeout_seconds=30,
            retry=0,
        )
    )
    db_session.commit()
    return {"plan": plan, "host": host, "device": device, "script": script}


class TestSingleDeviceDispatchPrecheck:
    def test_single_device_dispatch_precheck_pass(
        self, db_session, single_device_gate_chain
    ):
        chain = single_device_gate_chain
        pr = prepare_plan_run(
            plan_id=chain["plan"].id,
            device_ids=[chain["device"].id],
            triggered_by="testuser",
            db=db_session,
            run_type="MANUAL",
        )

        async def _fake_call(host_id, event, data, *, timeout=10.0):
            return _ack_ok(host_id, "aabbcc11")

        with patch(
            "backend.services.plan_precheck.call_agent_rpc",
            side_effect=_fake_call,
        ):
            asyncio.run(_drive_dispatch_gate(pr.id, db=db_session))

        db_session.expire_all()
        pr_after: PlanRun = db_session.get(PlanRun, pr.id)
        assert pr_after.status == "RUNNING"
        assert pr_after.run_context["precheck"]["phase"] == "ready"
        assert pr_after.run_context["dispatch_state"]["status"] == "completed"

        assert pr_after.run_context["idempotency_key"] == compute_idempotency_key(
            pr.id
        )
        assert pr_after.run_context["dispatch_payload_hash"] == (
            compute_dispatch_payload_hash(pr_after)
        )

        jobs = (
            db_session.query(JobInstance)
            .filter(JobInstance.plan_run_id == pr.id)
            .all()
        )
        assert len(jobs) == 1
        assert jobs[0].device_id == chain["device"].id
