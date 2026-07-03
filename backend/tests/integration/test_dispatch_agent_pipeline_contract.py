"""#47 — 跨层契约测试：dispatcher 真实落库的 pipeline_def 必须能被 agent
PipelineEngine 正确解析执行。

Plan 组装 → dispatch_plan_sync → JobInstance.pipeline_def 是唯一事实源
(见 CLAUDE.md「Plan 无 lifecycle 列」)；agent 侧 PipelineEngine.execute 是
唯一消费者。此前两侧只各自有单元测试，从未有测试证明"dispatcher 产出的真实
JSON 真的能喂给 PipelineEngine 跑通"——本文件补上这条链路断言。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.agent.pipeline_engine import PipelineEngine
from backend.models.enums import HostStatus
from backend.models.host import Device, Host
from backend.models.plan import Plan, PlanStep
from backend.models.script import Script
from backend.services.plan_dispatcher_sync import dispatch_plan_sync


class _FakeScriptRegistry:
    """Resolve every script name to the same on-disk file (test only needs one script)."""

    def __init__(self, path: str):
        self._path = path

    def resolve(self, name: str, version: str):
        assert name
        assert version
        return SimpleNamespace(
            script_id=1,
            name=name,
            version=version,
            script_type="python",
            nfs_path=self._path,
            content_sha256="c" * 64,
        )


async def _fake_gate_rpc(host_id: str, event: str, data: dict, *, timeout: float = 10.0):
    return {
        "host_id": host_id,
        "agent_version": "test",
        "results": [
            {
                "name": "check_device",
                "version": "1.0.0",
                "expected_sha": "abc",
                "actual_sha": "abc",
                "exists": True,
                "ok": True,
                "error": None,
            }
        ],
        "checked_at": "2026-05-07T10:00:00Z",
    }


@pytest.fixture
def _dispatch_fixture(db_session):
    host = Host(id="h-contract", hostname="h-contract", status=HostStatus.ONLINE.value)
    device = Device(serial="S-contract", host_id="h-contract", status="ONLINE")
    script = Script(
        name="check_device", script_type="python", version="1.0.0",
        nfs_path="/s/check_device.py", content_sha256="abc",
        default_params={"timeout": 30},
    )
    plan = Plan(name="contract-test-plan")
    db_session.add_all([host, device, script, plan])
    db_session.commit()

    db_session.add_all([
        PlanStep(
            plan_id=plan.id, step_key="init_check",
            script_name="check_device", script_version="1.0.0",
            stage="init", sort_order=0, timeout_seconds=10, retry=0,
        ),
        PlanStep(
            plan_id=plan.id, step_key="td_clean",
            script_name="check_device", script_version="1.0.0",
            stage="teardown", sort_order=0, timeout_seconds=10, retry=0,
        ),
    ])
    db_session.commit()
    return plan, device, host


def test_dispatcher_pipeline_def_is_executable_by_agent_pipeline_engine(
    db_session, _dispatch_fixture, tmp_path,
):
    plan, device, host = _dispatch_fixture

    with patch(
        "backend.services.precheck.verify.call_agent_rpc",
        side_effect=_fake_gate_rpc,
    ):
        pr = dispatch_plan_sync(
            plan_id=plan.id,
            device_ids=[device.id],
            triggered_by="pytest-contract",
            db=db_session,
        )

    from backend.models.job import JobInstance
    job = (
        db_session.query(JobInstance)
        .filter(JobInstance.plan_run_id == pr.id)
        .one()
    )
    pipeline_def = job.pipeline_def

    # Sanity on the contract shape itself before handing it to the agent.
    assert "lifecycle" in pipeline_def
    assert set(pipeline_def.keys()) <= {"lifecycle"}, "唯一顶层键必须是 lifecycle（stages/phases 已废弃）"

    script_path = tmp_path / "check_device.py"
    script_path.write_text(
        "import json\n"
        "print(json.dumps({'metrics': {'ok': True}}))\n",
        encoding="utf-8",
    )

    engine = PipelineEngine(
        adb=SimpleNamespace(adb_path="adb"),
        serial=device.serial,
        run_id=job.id,
        script_registry=_FakeScriptRegistry(str(script_path)),
    )

    result = engine.execute(pipeline_def)

    assert result.success is True, f"agent 未能执行 dispatcher 产出的真实 pipeline_def: {result.error_message}"
    # init + teardown 各一个 script:check_device 步骤都应被执行到。
    assert engine._shared["init_check"] == {"ok": True}
    assert engine._shared["td_clean"] == {"ok": True}
