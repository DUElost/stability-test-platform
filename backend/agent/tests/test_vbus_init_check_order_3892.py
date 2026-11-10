# -*- coding: utf-8 -*-
"""ADR-0063 D5 第 3 条：vbus 刷机作业先刷机、再做 init 残留检查（#3892）。

PlanRun 1242 中，派发时已关机的设备 B 在 init 残留检查（adb 探针）上等满 600s 后失败，
并记成欠账；它永远到不了刷机步骤。本文件钉住两条路径的顺序与 teardown 归属。
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.agent.contracts.vbus_flash import is_vbus_flash_init, is_vbus_flash_lifecycle
from backend.agent.pipeline_engine import PipelineEngine, StepResult

_FLASH = {"step_id": "flash", "action": "script:flash_firmware", "version": "1.4.0",
          "params": {"trigger_mode": "vbus_cycle"}, "timeout_seconds": 5}
_CHECK = {"step_id": "check", "action": "script:check_device", "version": "1.0.0",
          "params": {}, "timeout_seconds": 5}


def _engine():
    uploader = MagicMock()
    uploader.send.return_value = {"manual_action": None}
    mq = MagicMock()
    mq.connected = True
    engine = PipelineEngine(adb=MagicMock(), serial="S", run_id=1, log_dir="/tmp/test_logs",
                            mq_producer=mq, api_url=None, is_aborted=lambda: False,
                            patrol_heartbeat_uploader=uploader)
    engine._verify_device_lease = lambda: None
    engine._archive_logs = lambda: None
    return engine


_TEARDOWN = [{"step_id": "teardown_step", "action": "script:check_device", "version": "1.0.0",
              "params": {}, "timeout_seconds": 5}]


def _run(init, *, check_ok=True, check_raises=None, failing_step=None, lease_lost_before=None):
    engine = _engine()
    calls = []
    teardown = []

    def check():
        calls.append("return_check")
        if check_raises is not None:
            raise check_raises
        return StepResult(success=check_ok, error_message=None if check_ok else "init_return_check:failed")

    def steps(phase, defs):
        for step in defs:
            if step["step_id"] == lease_lost_before:
                return StepResult(success=False, error_message="device_lease_lost")
            calls.append(step["step_id"])
            engine._init_steps_started = getattr(engine, "_init_steps_started", 0) + 1
            if step["step_id"] == failing_step:
                return StepResult(success=False, error_message="step failed")
        return StepResult(success=True)

    def run_teardown(defs, **kwargs):
        teardown.append(defs)
        return StepResult(success=True, metadata={"teardown_status": "SUCCESS"})

    engine._check_init_returns = check
    engine._run_lifecycle_steps = steps
    engine._execute_teardown_best_effort = run_teardown
    pipeline_def = {"lifecycle": {"timeout_seconds": 1, "init": init, "teardown": _TEARDOWN}}
    with patch("backend.agent.pipeline_engine.time.sleep", return_value=None):
        if check_raises is not None:
            with pytest.raises(type(check_raises)):
                engine._execute_lifecycle(pipeline_def)
            return None, calls, teardown
        result = engine._execute_lifecycle(pipeline_def)
    return result, calls, teardown


@pytest.mark.parametrize("init,expected", [
    ([_FLASH, _CHECK], True),
    ([_CHECK, _FLASH], False),
    ([{**_FLASH, "params": {"trigger_mode": "adb"}}], False),
    ([{**_FLASH, "params": None}], False),
    ([], False),
    (None, False),
])
def test_predicate_matches_control_plane_shape(init, expected):
    assert is_vbus_flash_init(init) is expected
    assert is_vbus_flash_lifecycle({"init": init}) is expected


def test_vbus_job_flashes_before_return_check():
    result, calls, teardown = _run([_FLASH, _CHECK])
    assert calls == ["flash", "return_check", "check"]
    assert result.success, result.error_message
    assert teardown


def test_non_vbus_job_still_checks_first():
    result, calls, _ = _run([_CHECK, _FLASH])
    assert calls == ["return_check", "check", "flash"]
    assert result.success, result.error_message


def test_vbus_deferred_check_failure_stops_init_and_skips_own_teardown():
    result, calls, teardown = _run([_FLASH, _CHECK], check_ok=False)
    assert calls == ["flash", "return_check"]
    assert result.metadata["termination_reason"] == "init_failure"
    assert "init_return_check:failed" in result.error_message
    assert teardown == []


def test_vbus_flash_failure_skips_check_but_runs_teardown():
    result, calls, teardown = _run([_FLASH, _CHECK], failing_step="flash")
    assert calls == ["flash"]
    assert result.metadata["termination_reason"] == "init_failure"
    assert teardown, "flash step started, so this job owns its teardown"


def test_non_vbus_check_failure_still_skips_teardown():
    result, calls, teardown = _run([_CHECK], check_ok=False)
    assert calls == ["return_check"]
    assert teardown == []


def test_vbus_lease_lost_before_flash_starts_skips_teardown():
    # 复核 #3915 P1：刷机步骤尚未启动就丢租约时，本 job 没有任何动作，不得跑 teardown。
    result, calls, teardown = _run([_FLASH, _CHECK], lease_lost_before="flash")
    assert calls == []
    assert result.metadata["termination_reason"] == "abort"
    assert teardown == []


def test_vbus_deferred_check_exception_skips_teardown():
    # 复核 #3915 P2：推迟的检查抛出异常时，teardown 门必须保持关闭。
    import sqlite3
    _, calls, teardown = _run([_FLASH, _CHECK], check_raises=sqlite3.OperationalError("locked"))
    assert calls == ["flash", "return_check"]
    assert teardown == []


def _real_step_engine(action_result, *, is_aborted=lambda: False):
    """真实 _run_lifecycle_steps/_execute_step；只替换脚本执行体，钉住「已启动」计数的接线。"""
    engine = _engine()
    engine._is_aborted = is_aborted
    teardown = []
    engine._check_init_returns = lambda: StepResult(success=True)
    engine._resolve_action = lambda action, step: (lambda ctx: action_result)

    def run_teardown(defs, **kwargs):
        teardown.append(defs)
        return StepResult(success=True, metadata={"teardown_status": "SUCCESS"})

    engine._execute_teardown_best_effort = run_teardown
    return engine, teardown


def test_real_step_path_counts_started_flash_and_runs_teardown():
    engine, teardown = _real_step_engine(StepResult(success=False, exit_code=1, error_message="flash failed"))
    pipeline_def = {"lifecycle": {"timeout_seconds": 1, "init": [{**_FLASH, "retry": 0}], "teardown": _TEARDOWN}}
    with patch("backend.agent.pipeline_engine.time.sleep", return_value=None):
        result = engine._execute_lifecycle(pipeline_def)
    assert result.metadata["termination_reason"] == "init_failure"
    assert engine._init_steps_started >= 1
    assert teardown, "a started flash step owns this job's teardown"


def test_real_step_path_lease_lost_before_start_skips_teardown():
    engine, teardown = _real_step_engine(StepResult(success=True), is_aborted=lambda: True)
    pipeline_def = {"lifecycle": {"timeout_seconds": 1, "init": [_FLASH, _CHECK], "teardown": _TEARDOWN}}
    with patch("backend.agent.pipeline_engine.time.sleep", return_value=None):
        result = engine._execute_lifecycle(pipeline_def)
    assert result.metadata["termination_reason"] == "abort"
    assert engine._init_steps_started == 0
    assert teardown == []
