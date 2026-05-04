# -*- coding: utf-8 -*-
"""Integration tests for PipelineEngine lifecycle execution.

Covers verification tasks 7.1–7.4:
  7.1 — E2E lifecycle flow: init → patrol → teardown
  7.2 — Patrol timing (interval_seconds) and termination (timeout / cancel)
  7.3 — Teardown best-effort: partial failures don't block remaining steps
  7.4 — stop_process by process_name in teardown context
"""

import json
import time
from unittest.mock import MagicMock, patch, call

import pytest

from backend.agent.pipeline_engine import PipelineEngine, StepContext, StepResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(
    is_aborted=None,
    api_url=None,
    log_dir=None,
):
    """Create a PipelineEngine with mocked external dependencies."""
    adb = MagicMock()
    mq = MagicMock()
    mq.connected = True

    engine = PipelineEngine(
        adb=adb,
        serial="MOCK_SERIAL",
        run_id=999,
        log_dir=log_dir or "/tmp/test_logs",
        mq_producer=mq,
        api_url=api_url,
        is_aborted=is_aborted or (lambda: False),
    )
    return engine, adb, mq


def _minimal_lifecycle(
    timeout_seconds=10,
    interval_seconds=1,
    init_steps=None,
    patrol_steps=None,
    teardown_steps=None,
):
    """Build a minimal lifecycle pipeline_def for testing."""
    return {
        "lifecycle": {
            "timeout_seconds": timeout_seconds,
            "init": init_steps or [
                {"step_id": "init_step", "action": "script:check_device", "version": "1.0.0", "params": {}, "timeout_seconds": 5}
            ],
            "patrol": {
                "interval_seconds": interval_seconds,
                "steps": patrol_steps or [
                    {"step_id": "patrol_step", "action": "script:check_device", "version": "1.0.0", "params": {}, "timeout_seconds": 5}
                ],
            },
            "teardown": teardown_steps or [
                {"step_id": "teardown_step", "action": "script:check_device", "version": "1.0.0", "params": {}, "timeout_seconds": 5}
            ],
        }
    }


def _lifecycle_no_patrol(timeout_seconds=10):
    """Build a lifecycle pipeline_def without patrol (init → teardown only)."""
    return {
        "lifecycle": {
            "timeout_seconds": timeout_seconds,
            "init": [
                {"step_id": "init_step", "action": "script:check_device", "version": "1.0.0", "params": {}, "timeout_seconds": 5}
            ],
            "teardown": [
                {"step_id": "teardown_step", "action": "script:check_device", "version": "1.0.0", "params": {}, "timeout_seconds": 5}
            ],
        }
    }


# ===========================================================================
# 7.1 — E2E lifecycle flow: init → patrol → teardown
# ===========================================================================

class TestLifecycleE2EFlow:
    """Verify complete lifecycle execution: init → patrol loop → teardown."""

    @patch("backend.agent.pipeline_engine.time.sleep", return_value=None)
    @patch("backend.agent.pipeline_engine.time.time")
    def test_init_patrol_teardown_order(self, mock_time, mock_sleep):
        """Init runs first, then patrol loops, then teardown always runs."""
        engine, adb, mq = _make_engine()

        # Track execution order via _run_lifecycle_steps and _execute_teardown_best_effort
        execution_log = []

        # Simulate time: init at t=0, after init at t=1, patrol#1 starts at t=2,
        # after patrol#1 at t=3, sleep ends at t=4, patrol#2 check at t=12 (past 10s timeout)
        time_values = [0, 1, 2, 3, 4, 12, 12, 12]
        time_idx = [0]

        def mock_time_fn():
            idx = min(time_idx[0], len(time_values) - 1)
            time_idx[0] += 1
            return time_values[idx]

        mock_time.side_effect = mock_time_fn

        def mock_run_steps(phase, steps):
            first_step = (steps or [{}])[0]
            step_id = first_step.get("step_id", "unknown")
            execution_log.append(f"{phase}:{step_id}")
            return StepResult(success=True)

        def mock_teardown(teardown_def):
            execution_log.append("teardown")
            return StepResult(success=True, metadata={"teardown_status": "SUCCESS"})

        engine._run_lifecycle_steps = mock_run_steps
        engine._execute_teardown_best_effort = mock_teardown
        engine._verify_device_lease = lambda: None
        engine._archive_logs = lambda: None

        pipeline_def = _minimal_lifecycle(timeout_seconds=10, interval_seconds=5)

        result = engine._execute_lifecycle(pipeline_def)

        # Verify order: init first, then at least one patrol, then teardown last
        assert execution_log[0] == "init:init_step", "Init must run first"
        assert execution_log[-1] == "teardown", "Teardown must run last"
        assert any("patrol_step" in e for e in execution_log), "Patrol must run at least once"

    @patch("backend.agent.pipeline_engine.time.sleep", return_value=None)
    def test_lifecycle_without_patrol(self, mock_sleep):
        """When patrol is absent, lifecycle runs init → teardown directly."""
        engine, *_ = _make_engine()

        execution_log = []

        def mock_run_steps(phase, steps):
            first_step = (steps or [{}])[0]
            execution_log.append(first_step.get("step_id", "unknown"))
            return StepResult(success=True)

        def mock_teardown(teardown_def):
            execution_log.append("teardown")
            return StepResult(success=True, metadata={"teardown_status": "SUCCESS"})

        engine._run_lifecycle_steps = mock_run_steps
        engine._execute_teardown_best_effort = mock_teardown
        engine._verify_device_lease = lambda: None
        engine._archive_logs = lambda: None

        result = engine._execute_lifecycle(_lifecycle_no_patrol())

        assert result.success is True
        assert execution_log == ["init_step", "teardown"]

    @patch("backend.agent.pipeline_engine.time.sleep", return_value=None)
    @patch("backend.agent.pipeline_engine.time.time")
    def test_mq_status_reports(self, mock_time, mock_sleep):
        """Lifecycle emits INIT_RUNNING, PATROL_RUNNING, TEARDOWN_RUNNING, COMPLETED."""
        engine, adb, mq = _make_engine()

        # Time: init at t=0, after init at t=1, patrol at t=2, after patrol at t=3,
        # sleep check at t=15 (past 10s timeout)
        time_values = [0, 1, 2, 3, 4, 15, 15, 15]
        time_idx = [0]

        def mock_time_fn():
            idx = min(time_idx[0], len(time_values) - 1)
            time_idx[0] += 1
            return time_values[idx]

        mock_time.side_effect = mock_time_fn

        engine._run_lifecycle_steps = lambda phase, steps: StepResult(success=True)
        engine._execute_teardown_best_effort = lambda td: StepResult(
            success=True, metadata={"teardown_status": "SUCCESS"}
        )
        engine._verify_device_lease = lambda: None
        engine._archive_logs = lambda: None

        pipeline_def = _minimal_lifecycle(timeout_seconds=10, interval_seconds=5)
        engine._execute_lifecycle(pipeline_def)

        # Collect all MQ status calls
        status_calls = [c[0][1] for c in mq.send_job_status.call_args_list]
        assert "INIT_RUNNING" in status_calls
        assert "PATROL_RUNNING" in status_calls
        assert "TEARDOWN_RUNNING" in status_calls
        assert "COMPLETED" in status_calls

    @patch("backend.agent.pipeline_engine.time.sleep", return_value=None)
    @patch("backend.agent.pipeline_engine.time.time")
    def test_shared_context_persists_across_phases(self, mock_time, mock_sleep):
        """Shared context from init is available in patrol and teardown."""
        engine, *_ = _make_engine()

        # Time: init at t=0, after init at t=1, patrol at t=2, after patrol at t=3,
        # sleep check at t=15 (past 10s timeout)
        time_values = [0, 1, 2, 3, 4, 15, 15, 15]
        time_idx = [0]

        def mock_time_fn():
            idx = min(time_idx[0], len(time_values) - 1)
            time_idx[0] += 1
            return time_values[idx]

        mock_time.side_effect = mock_time_fn

        def init_steps():
            engine._shared["start_monkey"] = {"pid": 42}
            return StepResult(success=True)

        def patrol_steps():
            # Patrol should see the PID from init
            assert "start_monkey" in engine._shared
            assert engine._shared["start_monkey"]["pid"] == 42
            return StepResult(success=True)

        call_count = [0]

        def mock_run_steps(phase, steps):
            first_step = (steps or [{}])[0]
            step_id = first_step.get("step_id", "unknown")
            if "init" in step_id:
                return init_steps()
            else:
                call_count[0] += 1
                return patrol_steps()

        def mock_teardown(teardown_def):
            assert engine._shared["start_monkey"]["pid"] == 42
            return StepResult(success=True, metadata={"teardown_status": "SUCCESS"})

        engine._run_lifecycle_steps = mock_run_steps
        engine._execute_teardown_best_effort = mock_teardown
        engine._verify_device_lease = lambda: None
        engine._archive_logs = lambda: None

        pipeline_def = _minimal_lifecycle(timeout_seconds=10, interval_seconds=5)
        result = engine._execute_lifecycle(pipeline_def)
        assert result.success is True


# ===========================================================================
# 7.2 — Patrol timing and termination
# ===========================================================================

class TestPatrolTimingAndTermination:
    """Verify patrol respects interval_seconds and terminates correctly."""

    @patch("backend.agent.pipeline_engine.time.sleep", return_value=None)
    @patch("backend.agent.pipeline_engine.time.time")
    def test_timeout_terminates_patrol(self, mock_time, mock_sleep):
        """Patrol loop exits when timeout_seconds is reached."""
        engine, adb, mq = _make_engine()
        patrol_count = [0]

        # Simulate time progression: init_completed_at=0, first timeout check=11 (past 10s)
        time_seq = iter([0, 11, 11, 11, 11, 11])
        mock_time.side_effect = lambda: next(time_seq, 999)

        def mock_run_steps(phase, steps):
            first_step = (steps or [{}])[0]
            if "patrol" in first_step.get("step_id", ""):
                patrol_count[0] += 1
            return StepResult(success=True)

        engine._run_lifecycle_steps = mock_run_steps
        engine._execute_teardown_best_effort = lambda td: StepResult(
            success=True, metadata={"teardown_status": "SUCCESS"}
        )
        engine._verify_device_lease = lambda: None
        engine._archive_logs = lambda: None

        pipeline_def = _minimal_lifecycle(timeout_seconds=10, interval_seconds=60)
        result = engine._execute_lifecycle(pipeline_def)

        assert result.success is True  # timeout is a "normal" exit
        assert result.metadata["termination_reason"] == "timeout"
        # No patrol should run since timeout is already exceeded at check time
        assert patrol_count[0] == 0

    @patch("backend.agent.pipeline_engine.time.sleep", return_value=None)
    def test_cancel_terminates_patrol(self, mock_sleep):
        """Setting engine._canceled triggers abort and runs teardown."""
        engine, adb, mq = _make_engine()

        patrol_count = [0]

        def mock_run_steps(phase, steps):
            first_step = (steps or [{}])[0]
            if "patrol" in first_step.get("step_id", ""):
                patrol_count[0] += 1
                if patrol_count[0] >= 2:
                    engine._canceled = True
            return StepResult(success=True)

        engine._run_lifecycle_steps = mock_run_steps
        engine._execute_teardown_best_effort = lambda td: StepResult(
            success=True, metadata={"teardown_status": "SUCCESS"}
        )
        engine._verify_device_lease = lambda: None
        engine._archive_logs = lambda: None

        pipeline_def = _minimal_lifecycle(timeout_seconds=9999, interval_seconds=0.001)
        result = engine._execute_lifecycle(pipeline_def)

        assert result.metadata["termination_reason"] == "abort"
        assert patrol_count[0] >= 2

    @patch("backend.agent.pipeline_engine.time.sleep", return_value=None)
    def test_lock_lost_terminates_patrol(self, mock_sleep):
        """is_aborted() returning True triggers abort."""
        abort_flag = [False]
        engine, *_ = _make_engine(is_aborted=lambda: abort_flag[0])

        patrol_count = [0]

        def mock_run_steps(phase, steps):
            first_step = (steps or [{}])[0]
            if "patrol" in first_step.get("step_id", ""):
                patrol_count[0] += 1
                if patrol_count[0] >= 3:
                    abort_flag[0] = True
            return StepResult(success=True)

        engine._run_lifecycle_steps = mock_run_steps
        engine._execute_teardown_best_effort = lambda td: StepResult(
            success=True, metadata={"teardown_status": "SUCCESS"}
        )
        engine._verify_device_lease = lambda: None
        engine._archive_logs = lambda: None

        pipeline_def = _minimal_lifecycle(timeout_seconds=9999, interval_seconds=0.001)
        result = engine._execute_lifecycle(pipeline_def)

        assert result.metadata["termination_reason"] == "abort"
        assert not result.success  # abort is not "successful"

    @patch("backend.agent.pipeline_engine.time.sleep", return_value=None)
    def test_patrol_failure_terminates_loop(self, mock_sleep):
        """Patrol returning failure breaks the loop and triggers teardown."""
        engine, adb, mq = _make_engine()
        patrol_count = [0]

        def mock_run_steps(phase, steps):
            first_step = (steps or [{}])[0]
            if "patrol" in first_step.get("step_id", ""):
                patrol_count[0] += 1
                if patrol_count[0] >= 2:
                    return StepResult(success=False, error_message="device unreachable")
            return StepResult(success=True)

        teardown_called = [False]

        def mock_teardown(td):
            teardown_called[0] = True
            return StepResult(success=True, metadata={"teardown_status": "SUCCESS"})

        engine._run_lifecycle_steps = mock_run_steps
        engine._execute_teardown_best_effort = mock_teardown
        engine._verify_device_lease = lambda: None
        engine._archive_logs = lambda: None

        pipeline_def = _minimal_lifecycle(timeout_seconds=9999, interval_seconds=0.001)
        result = engine._execute_lifecycle(pipeline_def)

        assert result.metadata["termination_reason"] == "patrol_failure"
        assert teardown_called[0] is True
        assert "patrol #2 failed" in result.error_message

    @patch("backend.agent.pipeline_engine.time.sleep", return_value=None)
    def test_init_failure_triggers_teardown(self, mock_sleep):
        """Init failure skips patrol entirely but still runs teardown."""
        engine, adb, mq = _make_engine()
        teardown_called = [False]

        def mock_run_steps(phase, steps):
            return StepResult(success=False, error_message="init check_device failed")

        def mock_teardown(td):
            teardown_called[0] = True
            return StepResult(success=True, metadata={"teardown_status": "SUCCESS"})

        engine._run_lifecycle_steps = mock_run_steps
        engine._execute_teardown_best_effort = mock_teardown
        engine._verify_device_lease = lambda: None
        engine._archive_logs = lambda: None

        pipeline_def = _minimal_lifecycle()
        result = engine._execute_lifecycle(pipeline_def)

        assert result.metadata["termination_reason"] == "init_failure"
        assert teardown_called[0] is True
        assert not result.success

    @patch("backend.agent.pipeline_engine.time.sleep", return_value=None)
    @patch("backend.agent.pipeline_engine.time.time")
    def test_termination_reason_in_mq_event(self, mock_time, mock_sleep):
        """The TEARDOWN_RUNNING MQ event contains termination_reason."""
        engine, adb, mq = _make_engine()

        # Simulate: init at t=0, after init at t=1, patrol check at t=100 (past 10s timeout)
        time_seq = iter([0, 1, 100, 100, 100])
        mock_time.side_effect = lambda: next(time_seq, 999)

        engine._run_lifecycle_steps = lambda phase, steps: StepResult(success=True)
        engine._execute_teardown_best_effort = lambda td: StepResult(
            success=True, metadata={"teardown_status": "SUCCESS"}
        )
        engine._verify_device_lease = lambda: None
        engine._archive_logs = lambda: None

        pipeline_def = _minimal_lifecycle(timeout_seconds=10, interval_seconds=60)
        engine._execute_lifecycle(pipeline_def)

        # Find the TEARDOWN_RUNNING call
        teardown_calls = [
            c for c in mq.send_job_status.call_args_list
            if c[0][1] == "TEARDOWN_RUNNING"
        ]
        assert len(teardown_calls) == 1
        assert "termination_reason=timeout" in teardown_calls[0][0][2]


# ===========================================================================
# 7.3 — Teardown best-effort: partial step failures
# ===========================================================================

class TestTeardownBestEffort:
    """Verify teardown steps run independently: one failure doesn't block others."""

    def test_partial_failure_continues_execution(self):
        """When some teardown steps fail, remaining steps still execute."""
        engine, adb, mq = _make_engine()
        engine._verify_device_lease = lambda: None
        engine._archive_logs = lambda: None

        executed_steps = []

        def mock_execute_step(stage, step):
            step_id = step.get("step_id", "unknown")
            executed_steps.append(step_id)
            # Simulate device disconnect: first step fails
            if step_id == "stop_monkey":
                return StepResult(success=False, error_message="adb: device not found")
            return StepResult(success=True)

        engine._execute_step = mock_execute_step

        teardown_def = [
            {"step_id": "ensure_root", "action": "script:ensure_root", "version": "1.0.0", "params": {}, "timeout_seconds": 5},
            {"step_id": "stop_monkey", "action": "script:stop_process", "version": "1.0.0", "params": {}, "timeout_seconds": 5},
            {"step_id": "collect_bugreport", "action": "script:collect_bugreport", "version": "1.0.0", "params": {}, "timeout_seconds": 5},
            {"step_id": "scan_aee", "action": "script:scan_aee", "version": "1.0.0", "params": {}, "timeout_seconds": 5},
            {"step_id": "log_scan", "action": "script:log_scan", "version": "1.0.0", "params": {}, "timeout_seconds": 5},
        ]

        result = engine._execute_teardown_best_effort(teardown_def)

        # ALL steps should have executed despite stop_monkey failing
        assert executed_steps == ["ensure_root", "stop_monkey", "collect_bugreport", "scan_aee", "log_scan"]
        assert result.metadata["teardown_status"] == "DEGRADED"
        assert result.success is True  # DEGRADED still counts as success

    def test_all_steps_fail_returns_failed(self):
        """When all teardown steps fail, status is FAILED."""
        engine, *_ = _make_engine()

        def mock_execute_step(stage, step):
            return StepResult(success=False, error_message="adb: device not found")

        engine._execute_step = mock_execute_step

        teardown_def = [
            {"step_id": "step_a", "action": "script:check_device", "version": "1.0.0", "params": {}, "timeout_seconds": 5},
            {"step_id": "step_b", "action": "script:stop_process", "version": "1.0.0", "params": {}, "timeout_seconds": 5},
        ]

        result = engine._execute_teardown_best_effort(teardown_def)
        assert result.metadata["teardown_status"] == "FAILED"
        assert result.success is False

    def test_step_exception_continues_execution(self):
        """Even if a step raises an exception, subsequent steps still run."""
        engine, *_ = _make_engine()
        executed = []

        def mock_execute_step(stage, step):
            step_id = step.get("step_id", "unknown")
            executed.append(step_id)
            if step_id == "exploding_step":
                raise ConnectionError("adb connection reset by peer")
            return StepResult(success=True)

        engine._execute_step = mock_execute_step

        teardown_def = [
            {"step_id": "exploding_step", "action": "script:scan_aee", "version": "1.0.0", "params": {}, "timeout_seconds": 5},
            {"step_id": "after_explosion", "action": "script:log_scan", "version": "1.0.0", "params": {}, "timeout_seconds": 5},
            {"step_id": "final_step", "action": "script:adb_pull", "version": "1.0.0", "params": {}, "timeout_seconds": 5},
        ]

        result = engine._execute_teardown_best_effort(teardown_def)
        assert executed == ["exploding_step", "after_explosion", "final_step"]
        assert result.metadata["teardown_status"] == "DEGRADED"

    def test_all_succeed_returns_success(self):
        """When all teardown steps succeed, status is SUCCESS."""
        engine, *_ = _make_engine()

        engine._execute_step = lambda phase, step: StepResult(success=True)

        teardown_def = [
            {"step_id": "s1", "action": "script:check_device", "version": "1.0.0", "params": {}, "timeout_seconds": 5},
            {"step_id": "s2", "action": "script:stop_process", "version": "1.0.0", "params": {}, "timeout_seconds": 5},
            {"step_id": "s3", "action": "script:log_scan", "version": "1.0.0", "params": {}, "timeout_seconds": 5},
        ]

        result = engine._execute_teardown_best_effort(teardown_def)
        assert result.metadata["teardown_status"] == "SUCCESS"
        assert result.success is True
        assert result.exit_code == 0

    def test_device_disconnect_simulation(self):
        """Simulate device disconnect: ADB steps fail but non-ADB steps succeed."""
        engine, *_ = _make_engine()
        executed = []

        def mock_execute_step(stage, step):
            step_id = step.get("step_id", "unknown")
            executed.append(step_id)
            # Simulate: ADB-dependent steps fail, host-only steps succeed
            adb_steps = {"ensure_root", "stop_monkey", "collect_bugreport", "scan_aee", "export_mobilelogs", "adb_pull"}
            if step_id in adb_steps:
                return StepResult(success=False, error_message="adb: device offline")
            return StepResult(success=True)

        engine._execute_step = mock_execute_step

        teardown_def = [
            {"step_id": "ensure_root", "action": "script:ensure_root", "version": "1.0.0", "params": {}, "timeout_seconds": 5},
            {"step_id": "stop_monkey", "action": "script:stop_process", "version": "1.0.0", "params": {}, "timeout_seconds": 5},
            {"step_id": "collect_bugreport", "action": "script:collect_bugreport", "version": "1.0.0", "params": {}, "timeout_seconds": 5},
            {"step_id": "scan_aee", "action": "script:scan_aee", "version": "1.0.0", "params": {}, "timeout_seconds": 5},
            {"step_id": "export_mobilelogs", "action": "script:export_mobilelogs", "version": "1.0.0", "params": {}, "timeout_seconds": 5},
            {"step_id": "aee_extract", "action": "script:aee_extract", "version": "1.0.0", "params": {}, "timeout_seconds": 5},
            {"step_id": "log_scan", "action": "script:log_scan", "version": "1.0.0", "params": {}, "timeout_seconds": 5},
            {"step_id": "adb_pull", "action": "script:adb_pull", "version": "1.0.0", "params": {}, "timeout_seconds": 5},
        ]

        result = engine._execute_teardown_best_effort(teardown_def)

        # ALL 8 steps attempted despite device being offline
        assert len(executed) == 8
        assert result.metadata["teardown_status"] == "DEGRADED"
        # ensure_root failed (ADB-dependent), aee_extract + log_scan succeeded (host-only)
        assert result.success is True  # DEGRADED = at least one step succeeded

    @patch("backend.agent.pipeline_engine.time.sleep", return_value=None)
    def test_lifecycle_teardown_runs_even_on_init_exception(self, mock_sleep):
        """If init raises an unexpected exception, teardown still runs via try/finally."""
        engine, adb, mq = _make_engine()
        teardown_ran = [False]

        def exploding_steps(phase, steps):
            raise RuntimeError("unexpected init crash")

        def mock_teardown(td):
            teardown_ran[0] = True
            return StepResult(success=True, metadata={"teardown_status": "SUCCESS"})

        engine._run_lifecycle_steps = exploding_steps
        engine._execute_teardown_best_effort = mock_teardown
        engine._verify_device_lease = lambda: None
        engine._archive_logs = lambda: None

        pipeline_def = _minimal_lifecycle()

        # The exception should propagate but teardown should still have run
        with pytest.raises(RuntimeError, match="unexpected init crash"):
            engine._execute_lifecycle(pipeline_def)

        assert teardown_ran[0] is True


# ===========================================================================
# 7.4 — stop_process by process_name in teardown context
# ===========================================================================

class TestStopProcessInTeardown:
    """Verify stop_process works by process_name in teardown scenarios."""

    def test_teardown_template_uses_process_name(self):
        """The monkey_aee_teardown template's stop_monkey step uses process_name."""
        import os
        template_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "..", "schemas", "pipeline_templates", "monkey_aee_teardown.json",
        )
        with open(template_path, "r", encoding="utf-8") as f:
            template = json.load(f)

        # Find the stop_monkey step in execute stage
        execute_steps = template["stages"]["execute"]
        stop_step = next(s for s in execute_steps if s["step_id"] == "stop_monkey")

        assert stop_step["action"] == "builtin:stop_process"
        assert stop_step["params"]["process_name"] == "com.android.commands.monkey.transsion"
        # Should NOT have pid_from_step (teardown is cross-job, no shared PID)
        assert "pid_from_step" not in stop_step["params"]

    def test_lifecycle_template_teardown_uses_process_name(self):
        """The monkey_aee_lifecycle template's teardown stop_monkey uses process_name."""
        import os
        template_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "..", "schemas", "pipeline_templates", "monkey_aee_lifecycle.json",
        )
        with open(template_path, "r", encoding="utf-8") as f:
            template = json.load(f)

        teardown_execute = template["lifecycle"]["teardown"]["stages"]["execute"]
        stop_step = next(s for s in teardown_execute if s["step_id"] == "stop_monkey")

        assert stop_step["action"] == "builtin:stop_process"
        assert stop_step["params"]["process_name"] == "com.android.commands.monkey.transsion"

    def test_stop_process_by_name_kills_monkey(self):
        """stop_process with process_name calls pgrep -f and kills matching PIDs."""
        from backend.agent.actions.process_actions import stop_process

        adb = MagicMock()
        adb_calls = []

        def mock_shell(serial, cmd, timeout=30):
            adb_calls.append(cmd)
            if "pgrep" in cmd:
                return MagicMock(stdout="1234\n5678\n")
            return ""

        adb.shell = mock_shell

        ctx = StepContext(
            adb=adb,
            serial="DEVICE001",
            params={"process_name": "com.android.commands.monkey.transsion"},
            run_id=999,
            step_id=0,
            logger=MagicMock(),
            shared={},
        )

        result = stop_process(ctx)

        assert result.success is True
        # Should call: pgrep, kill 1234, kill 5678
        assert len(adb_calls) == 3
        assert "pgrep -f" in adb_calls[0]
        assert "com.android.commands.monkey.transsion" in adb_calls[0]
        assert "kill -9 1234" in adb_calls[1]
        assert "kill -9 5678" in adb_calls[2]

    def test_stop_process_by_name_no_match_is_idempotent(self):
        """stop_process with process_name when no process found returns success (teardown-safe)."""
        from backend.agent.actions.process_actions import stop_process

        adb = MagicMock()
        adb.shell = MagicMock(return_value=MagicMock(stdout=""))

        ctx = StepContext(
            adb=adb,
            serial="DEVICE001",
            params={"process_name": "com.android.commands.monkey.transsion"},
            run_id=999,
            step_id=0,
            logger=MagicMock(),
            shared={},
        )

        result = stop_process(ctx)
        assert result.success is True  # Idempotent: no error even if process already exited

    def test_stop_process_by_name_device_offline_is_handled(self):
        """stop_process gracefully handles ADB failure (device offline during teardown)."""
        from backend.agent.actions.process_actions import stop_process

        adb = MagicMock()
        adb.shell = MagicMock(side_effect=ConnectionError("adb: device offline"))

        ctx = StepContext(
            adb=adb,
            serial="DEVICE001",
            params={"process_name": "com.android.commands.monkey.transsion"},
            run_id=999,
            step_id=0,
            logger=MagicMock(),
            shared={},
        )

        result = stop_process(ctx)
        # pgrep exception → treated as "no match" → success (teardown idempotency)
        assert result.success is True
