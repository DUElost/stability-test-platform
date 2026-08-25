"""ADR-0030 v1.4 P1b（#404 PR-C）— 套件绑定：冻结 / 注入 / 五步门禁。

覆盖：
- prepare 冻结 ``run_context.dispatch_suite``（绑定存在才冻结；未绑定零字段）
- precheck 五步门禁矩阵 missing / not_exported / content_changed /
  sha_mismatch / project_mismatch 各一反一正（修复后放行）
- 门禁挂 admission 链的集成：fatal → FAILED(suite_verify_failed)；
  全绿 → RUNNING 且 job params 拿到注入
- mtbf_* 步骤参数注入 golden：expected_testpoint_count / project、已有值优先、
  未绑定 Plan 完全不受影响（env 回落仍在）
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import backend.core.admission_queue as admission_queue
from backend.models.enums import DeviceStatus, HostStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan, PlanStep
from backend.models.project import TestProject
from backend.models.script import Script
from backend.models.suite import TestCase, TestSuite
from backend.services.admission_pump import (
    admission_transaction,
    claim_queued_plan_runs,
)
from backend.services.plan_dispatcher_sync import prepare_plan_run
from backend.services.suite_binding import collect_suite_gate_error

pytestmark = pytest.mark.usefixtures("_v2_enabled")


@pytest.fixture
def _v2_enabled(monkeypatch):
    monkeypatch.setenv("STP_PLAN_ADMISSION_QUEUE_ENABLED", "1")
    admission_queue.mark_queue_pump_ready(True)
    yield
    admission_queue.mark_queue_pump_ready(False)


@pytest.fixture
def storage_root(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture
def bound_fixture(db_session, storage_root):
    """绑定 MTBF-legacy 套件的 Plan + host/device/script，可派发。"""
    host = Host(id="sb-h1", hostname="sb-h1", status=HostStatus.ONLINE.value)
    device = Device(serial="sb-d1", host_id="sb-h1",
                    status=DeviceStatus.ONLINE.value)
    setup_script = Script(
        name="mtbf_setup", script_type="python", version="1.3.0",
        nfs_path="/s/mtbf_setup.py", content_sha256="abc",
        default_params={},
    )
    check_script = Script(
        name="mtbf_check", script_type="python", version="1.2.0",
        nfs_path="/s/mtbf_check.py", content_sha256="def",
        default_params={},
    )
    suite = TestSuite(name="MTBF-legacy", root_config={}, apk_binding=["MTBF.apk"])
    db_session.add_all([host, device, setup_script, check_script, suite])
    db_session.flush()
    plan = Plan(name="sb-plan", suite=suite)
    db_session.add(plan)
    db_session.flush()
    db_session.add_all([
        PlanStep(plan_id=plan.id, step_key="init_setup",
                 script_name="mtbf_setup", script_version="1.3.0",
                 stage="init", sort_order=0, timeout_seconds=30, retry=0),
        PlanStep(plan_id=plan.id, step_key="td_check",
                 script_name="mtbf_check", script_version="1.2.0",
                 stage="teardown", sort_order=0, timeout_seconds=30, retry=0),
    ])
    db_session.commit()
    return {"plan": plan, "device": device, "host": host, "suite": suite}


def _add_case(db, suite, name="case-1", ordinal=1, enabled=True):
    db.add(TestCase(suite_id=suite.id, name=name, ordinal=ordinal,
                    enabled=enabled, times=1,
                    exec_descs=[{"class": "C", "method": "m"}]))
    db.commit()


def _export(db, suite) -> bytes:
    """模拟 export-to-tool-dir：落盘 runtask.xml + 写两个基线列。"""
    from backend.services.suite_binding import (
        current_content_fingerprint,
        runtask_disk_path,
    )
    payload = f"<runtask name='{suite.name}'/>".encode()
    path = runtask_disk_path(suite)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    suite.exported_sha256 = hashlib.sha256(payload).hexdigest()
    suite.exported_content_sha256 = current_content_fingerprint(db, suite)
    db.commit()
    return payload


def _queued_run(db, f):
    return prepare_plan_run(
        plan_id=f["plan"].id, device_ids=[f["device"].id],
        triggered_by="pytest", db=db, run_type="MANUAL",
    )


# ── prepare 冻结（§3.2）───────────────────────────────────────────────────────


class TestPrepareFreeze:
    def test_bound_run_freezes_dispatch_suite(self, db_session, bound_fixture):
        f = bound_fixture
        _add_case(db_session, f["suite"])
        _export(db_session, f["suite"])

        pr = _queued_run(db_session, f)

        frozen = pr.run_context["dispatch_suite"]
        assert frozen["suite_id"] == f["suite"].id
        assert frozen["suite_name"] == "MTBF-legacy"
        assert frozen["exported_sha256"] == f["suite"].exported_sha256
        assert (
            frozen["exported_content_sha256"]
            == f["suite"].exported_content_sha256
        )
        assert frozen["apk_binding"] == ["MTBF.apk"]
        assert frozen["export_dir"] == "legacy"

    def test_unbound_run_has_no_dispatch_suite(self, db_session, bound_fixture):
        """P0 存量兼容：未绑定 Plan 的 Run 不出现该字段。翻转硬拒后未绑定
        mtbf 派发已不存在，以非 mtbf 计划验证零字段不变量。"""
        f = bound_fixture
        f["plan"].suite_id = None
        for s in db_session.query(PlanStep).filter(
                PlanStep.plan_id == f["plan"].id).all():
            s.script_name = "check_device"
            s.script_version = "1.0.0"
        db_session.add(Script(
            name="check_device", script_type="python", version="1.0.0",
            nfs_path="/s/check_device.py", content_sha256="x",
            default_params={"timeout": 30},
        ))
        db_session.commit()

        pr = _queued_run(db_session, f)

        assert "dispatch_suite" not in (pr.run_context or {})

    def test_unbound_mtbf_dispatch_rejected(self, db_session, bound_fixture):
        """ADR-0030 v1.8 翻转：派发 mtbf 系脚本且未绑定 → prepare 硬拒
        （观测期零告警、唯一 mtbf Plan 已绑定后按 issue 口径翻转）。"""
        import pytest as _pytest

        from backend.services.plan_dispatcher_core import PlanDispatchError

        f = bound_fixture
        f["plan"].suite_id = None
        db_session.commit()

        with _pytest.raises(PlanDispatchError) as ei:
            _queued_run(db_session, f)

        assert "mtbf scripts require a bound suite" in str(ei.value)
        detail = ei.value.detail()
        assert detail["code"] == "SUITE_BINDING_REQUIRED"
        assert sorted(detail["mtbf_steps"]) == ["init_setup", "td_check"]

    def test_unbound_non_mtbf_plan_unaffected(self, db_session, bound_fixture):
        """翻转只针对 mtbf 脚本：未绑定的非 mtbf 计划照常派发。"""
        f = bound_fixture
        f["plan"].suite_id = None
        for s in db_session.query(PlanStep).filter(
                PlanStep.plan_id == f["plan"].id).all():
            s.script_name = "check_device"
            s.script_version = "1.0.0"
        db_session.add(Script(
            name="check_device", script_type="python", version="1.0.0",
            nfs_path="/s/check_device.py", content_sha256="x",
            default_params={"timeout": 30},
        ))
        db_session.commit()

        pr = _queued_run(db_session, f)   # 不抛即通过
        assert pr.status == "QUEUED"

    def test_bound_mtbf_dispatch_no_warning(self, db_session, bound_fixture, caplog):
        import logging

        f = bound_fixture
        with caplog.at_level(logging.WARNING, logger="backend.services.plan_dispatcher_sync"):
            _queued_run(db_session, f)

        assert not any(
            r.message.startswith("suite_unbound") for r in caplog.records)


# ── 五步门禁矩阵（§3.3，各一反一正）──────────────────────────────────────────


class TestSuiteGateMatrix:
    def test_step1_missing_then_active(self, db_session, bound_fixture):
        f = bound_fixture
        _export(db_session, f["suite"])
        f["suite"].is_active = False
        db_session.commit()
        pr = _queued_run(db_session, f)

        err = collect_suite_gate_error(db_session, pr)
        assert err is not None and err["step"] == "missing"

        f["suite"].is_active = True
        db_session.commit()
        assert collect_suite_gate_error(db_session, pr) is None

    def test_step2_not_exported_then_exported(self, db_session, bound_fixture):
        f = bound_fixture
        _add_case(db_session, f["suite"])
        pr = _queued_run(db_session, f)

        err = collect_suite_gate_error(db_session, pr)
        assert err is not None and err["step"] == "not_exported"

        # 修复路径：导出（两基线 + 磁盘文件）
        _export(db_session, f["suite"])
        assert collect_suite_gate_error(db_session, pr) is None

    def test_step2_disk_file_missing_is_not_exported(
        self, db_session, bound_fixture,
    ):
        f = bound_fixture
        _export(db_session, f["suite"])
        pr = _queued_run(db_session, f)

        from backend.services.suite_binding import runtask_disk_path
        runtask_disk_path(f["suite"]).unlink()

        err = collect_suite_gate_error(db_session, pr)
        assert err is not None and err["step"] == "not_exported"

    def test_step3_content_changed_then_reexport(self, db_session, bound_fixture):
        """「库改了没导出」由指纹**算出来**——任何内容变更路径都拦。"""
        f = bound_fixture
        _add_case(db_session, f["suite"])
        _export(db_session, f["suite"])
        pr = _queued_run(db_session, f)
        assert collect_suite_gate_error(db_session, pr) is None

        first = db_session.query(TestCase).filter(
            TestCase.suite_id == f["suite"].id).first()
        first.times = 42
        db_session.commit()

        err = collect_suite_gate_error(db_session, pr)
        assert err is not None and err["step"] == "content_changed"
        assert err["remedy"]  # 修复路径写进 detail

        # 重导刷新基线 → 放行
        _export(db_session, f["suite"])
        assert collect_suite_gate_error(db_session, pr) is None

    def test_step4_sha_mismatch_then_restore(self, db_session, bound_fixture):
        """「导出后磁盘被人动过」——setup trace 的 suite_sha256 与此闭环。"""
        f = bound_fixture
        _export(db_session, f["suite"])
        pr = _queued_run(db_session, f)

        from backend.services.suite_binding import runtask_disk_path
        path = runtask_disk_path(f["suite"])
        tampered = path.read_bytes() + b"<!-- patched -->"
        path.write_bytes(tampered)

        err = collect_suite_gate_error(db_session, pr)
        assert err is not None and err["step"] == "sha_mismatch"
        assert err["disk_sha256"] == hashlib.sha256(tampered).hexdigest()

        path.write_bytes(path.read_bytes().replace(b"<!-- patched -->", b""))
        assert collect_suite_gate_error(db_session, pr) is None

    def test_step5_project_mismatch_then_retarget(
        self, db_session, bound_fixture,
    ):
        f = bound_fixture
        p_cam = TestProject(project_key="CAM", display_name="Camera")
        p_other = TestProject(project_key="OTHER", display_name="Other")
        db_session.add_all([p_cam, p_other])
        db_session.flush()
        f["suite"].project_id = p_cam.id
        f["device"].project_id = p_other.id
        db_session.commit()
        _export(db_session, f["suite"])
        pr = _queued_run(db_session, f)

        err = collect_suite_gate_error(db_session, pr)
        assert err is not None and err["step"] == "project_mismatch"
        assert err["mismatched_devices"][0]["device_id"] == f["device"].id

        f["device"].project_id = p_cam.id
        db_session.commit()
        assert collect_suite_gate_error(db_session, pr) is None

    def test_step5_unassigned_device_fails_closed(self, db_session, bound_fixture):
        """SQL NULL 语义陷阱：未归属设备必须显式算不等，不得静默放行。"""
        f = bound_fixture
        p_cam = TestProject(project_key="CAM2", display_name="Camera2")
        db_session.add(p_cam)
        db_session.flush()
        f["suite"].project_id = p_cam.id
        f["device"].project_id = None      # NULL != CAM2 必须成立
        db_session.commit()
        _export(db_session, f["suite"])
        pr = _queued_run(db_session, f)

        err = collect_suite_gate_error(db_session, pr)
        assert err is not None and err["step"] == "project_mismatch"

    def test_generic_suite_passes_any_device(self, db_session, bound_fixture):
        """D3b 反向：通用套件（project 空）对任意设备放行。"""
        f = bound_fixture
        p_other = TestProject(project_key="OTHER2", display_name="Other2")
        db_session.add(p_other)
        db_session.flush()
        f["device"].project_id = p_other.id
        _export(db_session, f["suite"])
        pr = _queued_run(db_session, f)
        assert collect_suite_gate_error(db_session, pr) is None

    def test_unbound_plan_never_gated(self, db_session, bound_fixture):
        """查找键 = plan.suite_id：未绑定的 Plan 即使套件全坏也不进门禁。

        翻转硬拒后 prepare 不再产出未绑定 mtbf Run，直构裸 PlanRun 验证
        门禁函数的放行分支（防御性覆盖，语义不变）。
        """
        from backend.models.plan_run import PlanRun

        f = bound_fixture
        f["suite"].is_active = False   # 套件坏掉
        f["plan"].suite_id = None      # 但 Plan 已解绑
        db_session.commit()
        pr = PlanRun(plan_id=f["plan"].id, status="PRECHECK", run_type="MANUAL",
                     plan_snapshot={}, run_context={})
        db_session.add(pr)
        db_session.commit()
        assert collect_suite_gate_error(db_session, pr) is None


# ── admission 链集成 ─────────────────────────────────────────────────────────


class TestAdmissionIntegration:
    def _claim(self, db, pr) -> str:
        claimed = claim_queued_plan_runs(db)
        assert claimed and claimed[0][0] == pr.id
        return claimed[0][1]

    def test_gate_failure_fails_fatal_before_script_verify(
        self, db_session, bound_fixture,
    ):
        from unittest.mock import patch

        import asyncio
        from backend.models.plan_run import PlanRun
        from backend.services.admission_pump import plan_admission_task

        f = bound_fixture
        _add_case(db_session, f["suite"])
        pr = _queued_run(db_session, f)          # 从未导出 → not_exported
        attempt = self._claim(db_session, pr)
        db_session.expire_all()

        async def explode(*a, **k):
            raise AssertionError("script verify must not run after gate failure")

        with patch(
            "backend.services.precheck.verify.gather_verify", new=explode,
        ):
            asyncio.run(plan_admission_task({}, plan_run_id=pr.id, attempt_id=attempt))

        db_session.expire_all()
        pr = db_session.get(PlanRun, pr.id)
        assert pr.status == "FAILED"
        assert pr.result_summary["reason"] == "suite_verify_failed"
        assert pr.result_summary["step"] == "not_exported"

    def test_gate_pass_admits_and_injects_params(self, db_session, bound_fixture):
        from backend.models.plan_run import PlanRun

        f = bound_fixture
        _add_case(db_session, f["suite"], "case-1", ordinal=1)
        _add_case(db_session, f["suite"], "case-2", ordinal=2, enabled=False)
        _export(db_session, f["suite"])
        pr = _queued_run(db_session, f)
        attempt = self._claim(db_session, pr)

        assert admission_transaction(db_session, pr.id, attempt) is True

        db_session.expire_all()
        pr = db_session.get(PlanRun, pr.id)
        assert pr.status == "RUNNING"

        jobs = db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id).all()
        by_action = {
            step["action"]: step["params"]
            for job in jobs
            for _phase, step in _iter(job.pipeline_def)
        }
        setup_params = by_action["script:mtbf_setup"]
        # 启用计数：case-2 disabled 不计入
        assert setup_params["expected_testpoint_count"] == 1
        assert setup_params["project"] == "legacy"
        check_params = by_action["script:mtbf_check"]
        assert check_params["expected_testpoint_count"] == 1
        assert check_params["project"] == "legacy"


def _iter(pipeline: dict):
    lifecycle = pipeline["lifecycle"]
    for phase in ("init", "teardown"):
        for step in lifecycle.get(phase) or []:
            yield phase, step
    patrol = lifecycle.get("patrol")
    if isinstance(patrol, dict):
        for step in patrol.get("steps") or []:
            yield "patrol", step


# ── 参数注入 golden（§3.4）───────────────────────────────────────────────────


class TestInjectSuiteParams:
    def _materialize(self, db_session, f, *, setup_defaults=None):
        if setup_defaults is not None:
            for row in db_session.query(Script).filter(
                    Script.name == "mtbf_setup").all():
                row.default_params = dict(setup_defaults)
            db_session.commit()
        pr = _queued_run(db_session, f)
        claimed = claim_queued_plan_runs(db_session)
        attempt = claimed[0][1]
        assert admission_transaction(db_session, pr.id, attempt) is True
        db_session.expire_all()
        return [
            step
            for job in db_session.query(JobInstance).filter(
                JobInstance.plan_run_id == pr.id).all()
            for _p, step in _iter(job.pipeline_def)
        ]

    def test_existing_user_value_wins(self, db_session, bound_fixture):
        """注入不以用户声明为前提，但已声明的值优先（WiFi 先例同款）。"""
        steps = self._materialize(
            db_session, bound_fixture,
            setup_defaults={"expected_testpoint_count": 999},
        )
        setup = next(s for s in steps if s["action"] == "script:mtbf_setup")
        assert setup["params"]["expected_testpoint_count"] == 999
        assert setup["params"]["project"] == "legacy"   # 未声明的键照常注入

    def test_unbound_plan_not_injected(self, db_session, bound_fixture):
        """防御性覆盖：run_context 无 dispatch_suite 的 Run（翻转前 prepare 的
        存量行 / 异常路径）物化时零注入——env 回落兜底语义保持。"""
        from backend.models.plan_run import PlanRun
        from backend.services.plan_dispatcher_sync import (
            materialize_jobs_and_allocations,
        )

        f = bound_fixture
        # 翻转后 prepare 已拒绝未绑定 mtbf 派发，这里绕过 prepare 直构裸
        # PlanRun（无 dispatch_suite），只验证物化器的防御分支。
        pr = PlanRun(plan_id=f["plan"].id, status="RUNNING", run_type="MANUAL",
                     plan_snapshot={}, run_context={})
        db_session.add(pr)
        db_session.commit()

        lifecycle = {
            "init": [{
                "step_id": "init_setup", "action": "script:mtbf_setup",
                "version": "1.3.0", "params": {}, "retry": 0,
            }],
            "teardown": [],
        }
        materialize_jobs_and_allocations(
            db_session, pr, lifecycle, [f["device"].id],
            {f["device"].id: f["host"].id},
        )
        db_session.expire_all()

        job = db_session.query(JobInstance).filter(
            JobInstance.plan_run_id == pr.id).one()
        for _p, step in _iter(job.pipeline_def):
            assert "expected_testpoint_count" not in step["params"]
            assert "project" not in step["params"]

    def test_non_mtbf_steps_untouched(self, db_session, bound_fixture):
        db_session.add(Script(
            name="check_device", script_type="python", version="1.0.0",
            nfs_path="/s/check_device.py", content_sha256="x",
            default_params={"timeout": 30},
        ))
        db_session.flush()
        db_session.add(PlanStep(
            plan_id=bound_fixture["plan"].id, step_key="init_checkdev",
            script_name="check_device", script_version="1.0.0",
            stage="init", sort_order=-1, timeout_seconds=30, retry=0,
        ))
        db_session.commit()

        steps = self._materialize(db_session, bound_fixture)
        plain = next(s for s in steps if s["action"] == "script:check_device")
        assert "expected_testpoint_count" not in plain["params"]
        assert "project" not in plain["params"]
