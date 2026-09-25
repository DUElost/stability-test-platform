"""#47 — 跨层契约测试：dispatcher 真实落库的 pipeline_def 必须能被 agent
PipelineEngine 正确解析执行。

Plan 组装 → dispatch_plan_sync → admission → JobInstance.pipeline_def 是唯一事实源
(见 CLAUDE.md「Plan 无 lifecycle 列」)；agent 侧 PipelineEngine.execute 是
唯一消费者。此前两侧只各自有单元测试，从未有测试证明"dispatcher 产出的真实
JSON 真的能喂给 PipelineEngine 跑通"——本文件补上这条链路断言。
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.agent.operation_scheduler import OperationScheduler
from backend.agent.pipeline_engine import PipelineEngine
from backend.models.enums import HostStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.models.script import Script
from backend.services.admission_pump import claim_queued_plan_runs, plan_admission_task
from backend.services.plan_dispatcher_sync import dispatch_plan_sync


def _publish_package(packages_root: Path, name: str, version: str, files: dict[str, str]) -> str:
    """写站点布局 ``packages/{name}/{version}.tar.gz``，返回整包 sha256。

    ADR-0051 起 Agent 恒 strict：脚本只经「DB 包身份 → tools_cache」执行，没有
    ``package_sha256`` 的条目直接 ``PackageUnavailable``。所以契约夹具必须给出**真包**，
    而不是把 strict 关掉——后者验的是一条生产已不存在的路径。
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel, body in sorted(files.items()):
            data = body.encode("utf-8")
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    blob = buf.getvalue()
    (packages_root / name).mkdir(parents=True, exist_ok=True)
    (packages_root / name / f"{version}.tar.gz").write_bytes(blob)
    return hashlib.sha256(blob).hexdigest()


class _FakeScriptRegistry:
    """Resolve every script name to the same published package (test only needs one script)."""

    def __init__(self, entry_basename: str, package_sha256: str):
        self._entry_basename = entry_basename
        self._package_sha256 = package_sha256

    def resolve(self, name: str, version: str):
        assert name
        assert version
        return SimpleNamespace(
            script_id=1,
            name=name,
            version=version,
            script_type="python",
            nfs_path=f"/s/{self._entry_basename}",
            content_sha256="c" * 64,
            package_sha256=self._package_sha256,
        )


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
    db_session, _dispatch_fixture, tmp_path, monkeypatch,
):
    plan, device, host = _dispatch_fixture

    pr = dispatch_plan_sync(
        plan_id=plan.id,
        device_ids=[device.id],
        triggered_by="pytest-contract",
        db=db_session,
    )
    assert pr.status == "QUEUED"

    claimed = claim_queued_plan_runs(db_session)
    attempt = next(a for rid, a in claimed if rid == pr.id)

    async def fake_gather(host_ids, expected):
        return {hid: (True, [{"ok": True}], None) for hid in host_ids}

    with patch(
        "backend.services.precheck.verify.gather_verify", new=fake_gather,
    ):
        asyncio.run(
            plan_admission_task(
                {}, plan_run_id=pr.id, attempt_id=attempt,
            ),
        )

    db_session.expire_all()
    pr = db_session.get(PlanRun, pr.id)
    assert pr.status == "RUNNING"

    job = (
        db_session.query(JobInstance)
        .filter(JobInstance.plan_run_id == pr.id)
        .one()
    )
    pipeline_def = job.pipeline_def

    # Sanity on the contract shape itself before handing it to the agent.
    assert "lifecycle" in pipeline_def
    assert set(pipeline_def.keys()) <= {"lifecycle"}, "唯一顶层键必须是 lifecycle（stages/phases 已废弃）"

    packages_root = tmp_path / "packages"
    package_sha = _publish_package(
        packages_root, "check_device", "1.0.0",
        {"check_device.py": "import json\nprint(json.dumps({'metrics': {'ok': True}}))\n"},
    )
    monkeypatch.setenv("STP_PACKAGES_ROOT", str(packages_root))
    monkeypatch.setenv("STP_TOOLS_CACHE_ROOT", str(tmp_path / "tools_cache"))

    engine = PipelineEngine(
        adb=SimpleNamespace(adb_path="adb"),
        serial=device.serial,
        run_id=job.id,
        script_registry=_FakeScriptRegistry("check_device.py", package_sha),
        operation_scheduler=OperationScheduler(),
    )

    result = engine.execute(pipeline_def)

    assert result.success is True, f"agent 未能执行 dispatcher 产出的真实 pipeline_def: {result.error_message}"
    # init + teardown 各一个 script:check_device 步骤都应被执行到。
    assert engine._shared["init_check"] == {"ok": True}
    assert engine._shared["td_clean"] == {"ok": True}
