"""Plan dispatcher unit tests — ADR-0020."""

import asyncio
from unittest.mock import patch

import pytest

from backend.models.enums import HostStatus
from backend.models.host import Device, Host
from backend.models.plan import Plan, PlanStep
from backend.models.script import Script
from backend.services.plan_dispatcher_sync import (
    _build_lifecycle_from_steps,
    _build_preview,
    PlanDispatchError,
    dispatch_plan_sync,
    preview_plan_dispatch_sync,
)


# ── Pure-unit tests ─────────────────────────────────────────────────────

class TestBuildLifecycle:
    def test_init_and_teardown(self):
        plan = Plan(name="p")
        steps = [
            PlanStep(plan_id=1, step_key="s1", script_name="init_s",
                     script_version="1.0.0", stage="init", sort_order=0),
            PlanStep(plan_id=1, step_key="s2", script_name="td_s",
                     script_version="1.0.0", stage="teardown", sort_order=0),
        ]
        defaults = {("init_s", "1.0.0"): {"x": 1},
                    ("td_s", "1.0.0"): {"y": 2}}
        lc = _build_lifecycle_from_steps(plan, steps, defaults)
        assert len(lc["init"]) == 1
        assert lc["init"][0]["params"] == {"x": 1}
        assert lc["init"][0]["action"] == "script:init_s"
        assert len(lc["teardown"]) == 1
        assert lc["teardown"][0]["params"] == {"y": 2}

    def test_patrol_steps(self):
        plan = Plan(name="p", patrol_interval_seconds=30)
        steps = [
            PlanStep(plan_id=1, step_key="p1", script_name="patrol_s",
                     script_version="1.0.0", stage="patrol", sort_order=0),
        ]
        defaults = {("patrol_s", "1.0.0"): {}}
        lc = _build_lifecycle_from_steps(plan, steps, defaults)
        assert "patrol" in lc
        assert lc["patrol"]["interval_seconds"] == 30
        assert len(lc["patrol"]["steps"]) == 1

    def test_plan_timeout(self):
        plan = Plan(name="p", timeout_seconds=900)
        steps = [
            PlanStep(plan_id=1, step_key="s1", script_name="a",
                     script_version="1.0.0", stage="init", sort_order=0),
        ]
        defaults = {("a", "1.0.0"): {}}
        lc = _build_lifecycle_from_steps(plan, steps, defaults)
        assert lc["timeout_seconds"] == 900

    def test_sort_order_ordering(self):
        plan = Plan(name="p")
        steps = [
            PlanStep(plan_id=1, step_key="s3", script_name="a",
                     script_version="1.0.0", stage="init", sort_order=2),
            PlanStep(plan_id=1, step_key="s1", script_name="a",
                     script_version="1.0.0", stage="init", sort_order=0),
            PlanStep(plan_id=1, step_key="s2", script_name="a",
                     script_version="1.0.0", stage="init", sort_order=1),
        ]
        defaults = {("a", "1.0.0"): {}}
        lc = _build_lifecycle_from_steps(plan, steps, defaults)
        keys = [s["step_id"] for s in lc["init"]]
        assert keys == ["s1", "s2", "s3"]

    def test_disabled_steps_are_not_included(self):
        plan = Plan(name="p")
        steps = [
            PlanStep(plan_id=1, step_key="enabled", script_name="a",
                     script_version="1.0.0", stage="init", sort_order=0,
                     enabled=True),
            PlanStep(plan_id=1, step_key="disabled", script_name="a",
                     script_version="1.0.0", stage="init", sort_order=1,
                     enabled=False),
        ]
        lifecycle = _build_lifecycle_from_steps(plan, steps, {("a", "1.0.0"): {}})
        assert [s["step_id"] for s in lifecycle["init"]] == ["enabled"]


class TestBuildPreview:
    def test_preview_structure(self):
        plan = Plan(id=5, name="preview-plan")
        lifecycle = {"init": [{"step_id": "a"}], "teardown": []}
        preview = _build_preview(plan, lifecycle, [10, 20])
        assert preview["plan_id"] == 5
        assert preview["plan_name"] == "preview-plan"
        assert preview["device_count"] == 2
        assert preview["job_count"] == 2
        assert preview["total_steps"] == 1


# ── Integration tests ───────────────────────────────────────────────────

@pytest.fixture
def _plan_fixture(db_session):
    """Create a minimal Plan + PlanStep + Script + Device + Host for dispatch."""
    host = Host(id="h-disp", hostname="hdisp",
                status=HostStatus.ONLINE.value)
    device = Device(serial="S-disp", host_id="h-disp", status="ONLINE")
    script = Script(
        name="check_device", script_type="python", version="1.0.0",
        nfs_path="/s/check_device.py", content_sha256="abc",
        default_params={"timeout": 30},
    )
    plan = Plan(name="dispatch-test")
    db_session.add_all([host, device, script, plan])
    db_session.commit()

    step = PlanStep(
        plan_id=plan.id, step_key="init_check",
        script_name="check_device", script_version="1.0.0",
        stage="init", sort_order=0, timeout_seconds=30, retry=0,
    )
    teardown_step = PlanStep(
        plan_id=plan.id, step_key="td_clean",
        script_name="check_device", script_version="1.0.0",
        stage="teardown", sort_order=0, timeout_seconds=10, retry=0,
    )
    db_session.add_all([step, teardown_step])
    db_session.commit()
    return plan, device, host


def _mock_gate_rpc(host_id: str, expected_sha: str = "abc"):
    async def _fake_call(host_id, event, data, *, timeout=10.0):
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

    return _fake_call


class TestDispatchPlan:
    def test_dispatch_creates_plan_run_and_jobs(self, db_session, _plan_fixture):
        plan, device, host = _plan_fixture

        with patch(
            "backend.services.precheck.verify.call_agent_rpc",
            side_effect=_mock_gate_rpc("h-disp"),
        ):
            pr = dispatch_plan_sync(
                plan_id=plan.id,
                device_ids=[device.id],
                triggered_by="test",
                db=db_session,
            )

        assert pr.id is not None
        assert pr.plan_id == plan.id
        assert pr.status == "RUNNING"
        assert pr.run_type == "MANUAL"
        assert pr.failure_threshold == plan.failure_threshold
        assert pr.plan_snapshot["plan"]["id"] == plan.id
        assert pr.plan_snapshot["plan"]["name"] == plan.name
        assert pr.plan_snapshot["plan"]["failure_threshold"] == plan.failure_threshold
        assert [s["step_key"] for s in pr.plan_snapshot["steps"]] == [
            "init_check",
            "td_clean",
        ]
        assert pr.plan_snapshot["steps"][0]["default_params"] == {"timeout": 30}
        assert "lifecycle" not in pr.plan_snapshot
        assert pr.run_context["precheck"]["phase"] == "ready"
        assert pr.run_context["dispatch_state"]["status"] == "completed"

        from backend.models.job import JobInstance
        jobs = db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id
        ).all()
        assert len(jobs) == 1
        assert jobs[0].device_id == device.id
        assert jobs[0].plan_id == plan.id
        assert jobs[0].status == "PENDING"

        # #47: 落库的 pipeline_def["lifecycle"] 必须与 _build_lifecycle_from_steps
        # 组装结果一致 —— 派发链路 (dispatch_plan_sync → resolved_pipeline) 不能
        # 悄悄偏离 lifecycle 组装的唯一事实源。
        steps = (
            db_session.query(PlanStep)
            .filter(PlanStep.plan_id == plan.id)
            .order_by(PlanStep.sort_order)
            .all()
        )
        expected_lifecycle = _build_lifecycle_from_steps(
            plan, steps, {("check_device", "1.0.0"): {"timeout": 30}},
        )
        assert jobs[0].pipeline_def == {"lifecycle": expected_lifecycle}

        # 结构层面同时锁定 ADR-0020 唯一 action 契约（script:<name>），防止
        # 未来误改回 builtin:/stages 等已废弃格式。
        init_step = jobs[0].pipeline_def["lifecycle"]["init"][0]
        assert init_step["action"] == "script:check_device"
        assert init_step["params"] == {"timeout": 30}
        assert init_step["step_id"] == "init_check"
        teardown_step = jobs[0].pipeline_def["lifecycle"]["teardown"][0]
        assert teardown_step["action"] == "script:check_device"
        assert teardown_step["step_id"] == "td_clean"

    def test_preview_returns_structure(self, db_session, _plan_fixture):
        plan, device, host = _plan_fixture

        preview = preview_plan_dispatch_sync(
            plan_id=plan.id,
            device_ids=[device.id],
            db=db_session,
        )

        assert preview["plan_id"] == plan.id
        assert preview["plan_name"] == plan.name
        assert preview["device_count"] == 1
        assert preview["job_count"] == 1
        assert "lifecycle" in preview


class TestValidation:
    def test_missing_plan_raises(self, db_session):
        with pytest.raises(PlanDispatchError, match="not found"):
            dispatch_plan_sync(
                plan_id=99999,
                device_ids=[1],
                triggered_by="test",
                db=db_session,
            )


# ── ADR-0023 C1: fail-fast on missing script metadata ───────────────────


class TestPlanDispatchErrorDetail:
    """``PlanDispatchError.detail()`` 为端点层的统一格式化入口。"""

    def test_detail_with_missing_scripts(self):
        exc = PlanDispatchError(
            "scripts unavailable: a:1.0.0",
            missing_scripts=["a:1.0.0", "b:v2"],
        )
        d = exc.detail()
        assert d == {"code": "INVALID_SCRIPT_REFS", "missing": ["a:1.0.0", "b:v2"]}

    def test_detail_without_missing_scripts_falls_back_to_str(self):
        exc = PlanDispatchError("plan not found")
        assert exc.detail() == "plan not found"

    def test_detail_with_mixed_watcher_activity(self):
        exc = PlanDispatchError(
            "watch激活与不激活的节点不能同时在一个计划中",
            mixed_watcher_inactive_host_ids=["host-101", "host-203"],
        )
        assert exc.detail() == {
            "code": "MIXED_WATCHER_ACTIVITY",
            "message": "watch激活与不激活的节点不能同时在一个计划中",
            "inactive_host_ids": ["host-101", "host-203"],
        }


@pytest.fixture
def _failfast_fixture(db_session):
    """C1 fail-fast 共享 fixture:Plan 引用一个 *存在但即将失活* 的 Script。"""
    host = Host(id="h-failfast", hostname="hff",
                status=HostStatus.ONLINE.value)
    device = Device(serial="S-failfast", host_id="h-failfast", status="ONLINE")
    script = Script(
        name="check_device", script_type="python", version="1.0.0",
        nfs_path="/nfs/scripts/check_device/v1.0.0/check_device.py",
        content_sha256="ff" * 32,
        default_params={"timeout": 30}, is_active=True,
    )
    plan = Plan(name="failfast-test")
    db_session.add_all([host, device, script, plan])
    db_session.commit()

    db_session.add(PlanStep(
        plan_id=plan.id, step_key="init_check",
        script_name="check_device", script_version="1.0.0",
        stage="init", sort_order=0, timeout_seconds=30, retry=0,
    ))
    db_session.commit()
    return plan, device, host, script


class TestFailFastSyncDispatch:
    def test_preview_rejects_deactivated_script(self, db_session, _failfast_fixture):
        from backend.services.plan_dispatcher_sync import preview_plan_dispatch_sync

        plan, device, _host, script = _failfast_fixture
        script.is_active = False
        db_session.commit()

        with pytest.raises(PlanDispatchError) as excinfo:
            preview_plan_dispatch_sync(
                plan_id=plan.id, device_ids=[device.id], db=db_session,
            )
        assert excinfo.value.missing_scripts == ["check_device:1.0.0"]

    def test_prepare_rejects_deactivated_no_plan_run_row(
        self, db_session, _failfast_fixture,
    ):
        from backend.services.plan_dispatcher_sync import prepare_plan_run

        plan, device, _host, script = _failfast_fixture
        script.is_active = False
        db_session.commit()

        with pytest.raises(PlanDispatchError) as excinfo:
            prepare_plan_run(
                plan_id=plan.id, device_ids=[device.id],
                triggered_by="test", db=db_session,
            )
        assert excinfo.value.missing_scripts == ["check_device:1.0.0"]

        # PlanRun 行没有被创建:fail-fast 必须在 INSERT 之前。
        from backend.models.plan_run import PlanRun
        assert db_session.query(PlanRun).filter(
            PlanRun.plan_id == plan.id
        ).count() == 0

    def test_prepare_rejects_nonexistent_script(self, db_session, _failfast_fixture):
        from backend.services.plan_dispatcher_sync import prepare_plan_run

        plan, device, _host, _script = _failfast_fixture
        # 加一个引用根本不存在的 (name, version) 的 step
        db_session.add(PlanStep(
            plan_id=plan.id, step_key="bogus",
            script_name="ghost_script", script_version="9.9.9",
            stage="init", sort_order=1, timeout_seconds=10, retry=0,
        ))
        db_session.commit()

        with pytest.raises(PlanDispatchError) as excinfo:
            prepare_plan_run(
                plan_id=plan.id, device_ids=[device.id],
                triggered_by="test", db=db_session,
            )
        # 不存在与 deactivate 在 keys 缺失这一层统一表达
        assert "ghost_script:9.9.9" in (excinfo.value.missing_scripts or [])

    def test_prepare_partial_missing_lists_only_missing(
        self, db_session, _failfast_fixture,
    ):
        """三个 step 中只有一个的脚本失活,missing_scripts 精确为长度 1。"""
        from backend.services.plan_dispatcher_sync import prepare_plan_run

        plan, device, _host, _script = _failfast_fixture
        # 再加两个引用其他 active 脚本的 step
        good_a = Script(
            name="aux_a", script_type="python", version="1.0.0",
            nfs_path="/nfs/scripts/aux_a/v1.0.0/aux_a.py",
            content_sha256="aa" * 32, default_params={},
            param_schema={}, is_active=True,
        )
        good_b = Script(
            name="aux_b", script_type="python", version="1.0.0",
            nfs_path="/nfs/scripts/aux_b/v1.0.0/aux_b.py",
            content_sha256="bb" * 32, default_params={},
            param_schema={}, is_active=True,
        )
        db_session.add_all([good_a, good_b])
        db_session.add_all([
            PlanStep(
                plan_id=plan.id, step_key="aux_a_step",
                script_name="aux_a", script_version="1.0.0",
                stage="init", sort_order=1, timeout_seconds=10, retry=0,
            ),
            PlanStep(
                plan_id=plan.id, step_key="aux_b_step",
                script_name="aux_b", script_version="1.0.0",
                stage="teardown", sort_order=0, timeout_seconds=10, retry=0,
            ),
        ])
        # 失活其中第一个 step 的脚本
        good_a.is_active = False
        db_session.commit()

        with pytest.raises(PlanDispatchError) as excinfo:
            prepare_plan_run(
                plan_id=plan.id, device_ids=[device.id],
                triggered_by="test", db=db_session,
            )
        assert excinfo.value.missing_scripts == ["aux_a:1.0.0"]

    def test_complete_uses_snapshot_when_script_deactivated_after_prepare(
        self, db_session, _failfast_fixture,
    ):
        """prepare 后 live Script 失活不改变当前运行的不可变快照。"""
        from backend.services.plan_dispatcher_sync import (
            complete_plan_run_dispatch, prepare_plan_run,
        )
        from backend.models.audit import AuditLog
        from backend.models.job import JobInstance

        plan, device, _host, script = _failfast_fixture
        pr = prepare_plan_run(
            plan_id=plan.id, device_ids=[device.id],
            triggered_by="test", db=db_session,
        )
        pr.status = "RUNNING"
        db_session.commit()

        # 模拟时间窗内脚本被失活
        script.is_active = False
        db_session.commit()

        complete_plan_run_dispatch(pr.id, db=db_session)

        db_session.refresh(pr)
        assert pr.status == "RUNNING"
        assert pr.ended_at is None
        assert pr.result_summary is None
        jobs = db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id
        ).all()
        assert len(jobs) == 1
        assert jobs[0].pipeline_def["lifecycle"]["init"][0]["action"] == (
            "script:check_device"
        )

        audit_count = db_session.query(AuditLog).filter(
            AuditLog.action == "plan_dispatch_failed",
            AuditLog.resource_id == str(pr.id),
        ).count()
        assert audit_count == 0

    def test_plan_snapshot_includes_nfs_path(self, db_session, _failfast_fixture):
        """ADR-0023 D3 实施前提:plan_snapshot.steps[i] 持有 nfs_path,为 DeviceDetailDrawer 准备。"""
        from backend.services.plan_dispatcher_sync import prepare_plan_run

        plan, device, _host, script = _failfast_fixture
        pr = prepare_plan_run(
            plan_id=plan.id, device_ids=[device.id],
            triggered_by="test", db=db_session,
        )
        step_entries = pr.plan_snapshot["steps"]
        assert step_entries[0]["nfs_path"] == script.nfs_path


class TestFailFastAsyncDispatch:
    """ADR-0023 C1 — async dispatcher 路径(SCHEDULE / async CHAIN)对偶覆盖。

    Note: async ``dispatch_plan`` 端到端需要 ``AsyncSessionLocal`` 与测试 sync
    session 共享提交可见性。SQLite + sync 事务回滚 fixture 下两边连接不互通
    (sync 持有未 COMMIT 的 transaction,asyncpg/aiosqlite 看不到 Plan 行),所以
    端到端覆盖留给真实 PG 集成测试。这里仅以 unit 方式确认 async 模块的
    ``_check_script_keys_complete`` 与 ``PlanDispatchError`` 行为与 sync 路径
    线对线一致。
    """

    def test_async_check_script_keys_complete_returns_missing(self):
        from backend.services.plan_dispatcher import _check_script_keys_complete

        steps = [
            PlanStep(plan_id=1, step_key="a", script_name="alpha",
                     script_version="1.0.0", stage="init", sort_order=0),
            PlanStep(plan_id=1, step_key="b", script_name="beta",
                     script_version="2.0.0", stage="init", sort_order=1),
        ]
        metadata = {("alpha", "1.0.0"): {"default_params": {}}}
        missing = _check_script_keys_complete(steps, metadata)
        assert missing == ["beta:2.0.0"]

    def test_async_plan_dispatch_error_detail_shape(self):
        from backend.services.plan_dispatcher import (
            PlanDispatchError as AsyncPlanDispatchError,
        )

        exc = AsyncPlanDispatchError(
            "scripts unavailable: foo:1",
            missing_scripts=["foo:1"],
        )
        assert exc.detail() == {"code": "INVALID_SCRIPT_REFS", "missing": ["foo:1"]}
        assert AsyncPlanDispatchError("plan not found").detail() == "plan not found"


class TestDispatcherCoreSharing:
    def test_sync_and_async_dispatchers_reexport_shared_core_helpers(self):
        from backend.services import plan_dispatcher_core
        from backend.services import plan_dispatcher as async_dispatcher
        from backend.services import plan_dispatcher_sync as sync_dispatcher

        assert (
            async_dispatcher.PlanDispatchError
            is plan_dispatcher_core.PlanDispatchError
        )
        assert (
            sync_dispatcher.PlanDispatchError
            is plan_dispatcher_core.PlanDispatchError
        )
        assert (
            async_dispatcher._build_lifecycle_from_steps
            is plan_dispatcher_core.build_lifecycle_from_steps
        )
        assert (
            sync_dispatcher._build_lifecycle_from_steps
            is plan_dispatcher_core.build_lifecycle_from_steps
        )
        assert (
            async_dispatcher._build_plan_snapshot
            is plan_dispatcher_core.build_plan_snapshot
        )
        assert (
            sync_dispatcher._build_plan_snapshot
            is plan_dispatcher_core.build_plan_snapshot
        )
