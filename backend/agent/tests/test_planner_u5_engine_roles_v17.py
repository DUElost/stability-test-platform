"""#3730 U5 v1.7：Engine 选择/归属按实际版本（§4.1 真入口对照）。"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from backend.agent.contracts.device_return import (
    DISARM_RESULT_EXECUTED_UNVERIFIED,
    DISARM_RESULT_NOT_EXECUTED,
    REASON_UNKNOWN_LEGACY_VERSION,
    STEP_ROLE_COLLECT,
    script_name_from_step,
    select_disarm_steps,
    step_teardown_role,
    step_version_of,
    version_key,
)
from backend.agent.pipeline_engine import PipelineEngine, StepResult


class _FakePermit:
    def release(self) -> None:
        return None


class _FakeScheduler:
    def acquire(self, device_id, timeout=None):
        del device_id, timeout
        return _FakePermit()


def _bare_engine(**kwargs):
    engine = PipelineEngine.__new__(PipelineEngine)
    engine._scheduler = kwargs.get("scheduler")
    engine._device_id = kwargs.get("device_id", 1)
    engine._canceled = False
    engine._is_aborted = kwargs.get("is_aborted", lambda: False)
    engine._is_lock_lost = kwargs.get("is_lock_lost", lambda: False)
    engine._run_id = kwargs.get("run_id", 1)
    engine._api_url = None
    engine._agent_secret = ""
    engine._fencing_token = "tok"
    engine._footprint_families = {"power-cycle"}
    engine._disarm_results = {}
    engine._lease_lost_for_disarm = False
    engine._active_phase = None
    engine._active_process = None
    engine._process_lock = threading.Lock()
    engine._update_execution_state = lambda *_a, **_k: None
    engine._last_step_error = ""
    engine._log_dir = None
    engine._adb = None
    engine._serial = "test-serial"
    engine._shared = {}
    engine._local_db = None
    engine._adb_path = ""
    engine._nfs_root = ""
    engine._make_mq_logger = lambda *a, **k: MagicMock()
    engine._report_step_trace_mq = MagicMock()
    engine._teardown_lease_lost = lambda: False
    return engine


class TestEngineVersionRoles:
    def test_manual_exit_selects_legacy_finish_1_0_8(self):
        teardown = [
            {"step_id": "d", "action": "script:powercycle_disarm", "version": "1.0.5"},
            {"step_id": "f", "action": "script:powercycle_finish", "version": "1.0.8"},
        ]
        selected = select_disarm_steps(teardown, {"power-cycle"})
        assert [s["step_id"] for s in selected] == ["d", "f"]

    def test_manual_exit_skips_finish_1_1_0(self):
        teardown = [
            {"step_id": "d", "action": "script:powercycle_disarm", "version": "1.0.5"},
            {"step_id": "f", "action": "script:powercycle_finish", "version": "1.1.0"},
        ]
        selected = select_disarm_steps(teardown, {"power-cycle"})
        assert [s["step_id"] for s in selected] == ["d"]

    def test_collect_failure_does_not_overwrite_disarm(self):
        engine = _bare_engine()
        engine._disarm_results = {
            "power-cycle": {
                "result": DISARM_RESULT_EXECUTED_UNVERIFIED,
                "step_id": "d",
                "exit_code": 0,
                "readback_summary": "clear",
            }
        }
        assert step_teardown_role("powercycle_finish", "1.1.0") == STEP_ROLE_COLLECT
        engine._record_disarm_for_step(
            {"step_id": "f", "action": "script:powercycle_finish", "version": "1.1.0"},
            started=True,
            success=False,
            exit_code=1,
            error_message="collect failed",
        )
        assert engine._disarm_results["power-cycle"]["result"] == DISARM_RESULT_EXECUTED_UNVERIFIED
        assert engine._disarm_results["power-cycle"]["step_id"] == "d"

class TestUnknownNumericFinishRealTeardown:
    """v1.7.8 F2：未登记的数字 finish 版本经真实 teardown 执行并记原因。"""

    @staticmethod
    def _engine(executed: list[str]):
        engine = _bare_engine()
        # 真实 permit 包装 + 真实 teardown 选择/归属，只替换脚本执行边界。
        engine._scheduler = _FakeScheduler()

        def resolve(action, step):
            def run(ctx):
                executed.append(str(step.get("step_id")))
                return StepResult(
                    success=True,
                    exit_code=0,
                    output='{"disarm_result":"executed_unverified","readback":"clear"}',
                )

            return run

        engine._resolve_action = resolve
        return engine

    @pytest.mark.parametrize("version", ["1.1.1", "2.0.0"])
    def test_unknown_numeric_finish_is_executed_as_disarm(self, version):
        executed: list[str] = []
        engine = self._engine(executed)
        result = engine._execute_teardown_best_effort(
            [{"step_id": "f", "action": "script:powercycle_finish", "version": version}],
            disarm_only=True,
        )
        assert executed == ["f"]
        assert result.success is True
        entry = engine._disarm_results["power-cycle"]
        assert entry["result"] == DISARM_RESULT_EXECUTED_UNVERIFIED
        assert entry["reason"] == REASON_UNKNOWN_LEGACY_VERSION

    def test_legacy_ge_1_1_rule_would_skip_unknown_finish(self, monkeypatch):
        """变异自证：回退「>=1.1 即收取」时同一入口不执行、保留 not_executed。"""
        import backend.agent.pipeline_engine as pe

        def legacy_select(teardown_def, families, *, onboarded=None):
            selected = []
            for step in teardown_def or []:
                if script_name_from_step(step) == "powercycle_finish":
                    vk = version_key(step_version_of(step))
                    if len(vk) >= 2 and vk[:2] >= (1, 1):
                        continue
                selected.append(step)
            return selected

        monkeypatch.setattr(pe, "select_disarm_steps", legacy_select)
        executed: list[str] = []
        engine = self._engine(executed)
        engine._execute_teardown_best_effort(
            [{"step_id": "f", "action": "script:powercycle_finish", "version": "1.1.1"}],
            disarm_only=True,
        )
        assert executed == []
        assert engine._disarm_results["power-cycle"]["result"] == DISARM_RESULT_NOT_EXECUTED

    def test_registered_collect_finish_is_not_executed(self):
        executed: list[str] = []
        engine = self._engine(executed)
        engine._execute_teardown_best_effort(
            [{"step_id": "f", "action": "script:powercycle_finish", "version": "1.1.0"}],
            disarm_only=True,
        )
        assert executed == []
        assert engine._disarm_results["power-cycle"]["result"] == DISARM_RESULT_NOT_EXECUTED


    def test_legacy_finish_records_disarm_result(self):
        engine = _bare_engine()
        engine._record_disarm_for_step(
            {"step_id": "f", "action": "script:powercycle_finish", "version": "1.0.8"},
            started=True,
            success=True,
            exit_code=0,
            output='{"disarm_result":"executed_unverified","readback":"clear"}',
        )
        assert engine._disarm_results["power-cycle"]["result"] == DISARM_RESULT_EXECUTED_UNVERIFIED
