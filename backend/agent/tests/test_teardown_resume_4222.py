"""Regression tests for Issue #4222 Problem 2 (teardown resume & disarm evidence cleanup)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.agent import pipeline_engine as pe_mod
from backend.agent.coordinator import HostRunCoordinator
from backend.agent.operation_scheduler import OperationScheduler
from backend.agent.pipeline_engine import PipelineEngine, StepResult
from backend.agent.pipeline_runner import execute_pipeline_run
from backend.agent.registry.patrol_checkpoint_store import PatrolCycleCheckpointStore


def _lifecycle_pipeline() -> dict:
    return {
        "lifecycle": {
            "timeout_seconds": 60,
            "init": [
                {
                    "step_id": "powercycle_setup",
                    "action": "script:powercycle_setup",
                    "version": "1.2.4",
                    "params": {},
                }
            ],
            "patrol": {
                "interval_seconds": 5,
                "steps": [
                    {
                        "step_id": "powercycle_check",
                        "action": "script:powercycle_check",
                        "version": "1.0.2",
                        "params": {},
                    }
                ],
            },
            "teardown": [
                {
                    "step_id": "powercycle_disarm",
                    "action": "script:powercycle_disarm",
                    "version": "1.0.8",
                    "params": {"mode": "disarm"},
                    "timeout_seconds": 660,
                },
                {
                    "step_id": "powercycle_finish",
                    "action": "script:powercycle_finish",
                    "version": "1.1.0",
                    "params": {},
                    "timeout_seconds": 420,
                },
            ],
        }
    }


def _make_engine(store: PatrolCycleCheckpointStore | None) -> PipelineEngine:
    uploader = MagicMock()
    uploader.send.return_value = {"manual_action": None}
    engine = PipelineEngine(
        adb=MagicMock(),
        serial="MOCK-SERIAL",
        run_id=211040,
        log_dir="/tmp/logs",
        mq_producer=MagicMock(connected=True),
        api_url=None,
        is_aborted=lambda: False,
        patrol_heartbeat_uploader=uploader,
        patrol_cycle_checkpoint_store=store,
    )
    engine._verify_device_lease = lambda: None
    engine._archive_logs = lambda: None
    engine._teardown_lease_lost = lambda: False
    return engine


@patch("backend.agent.pipeline_engine.time.sleep", return_value=None)
def test_patrol_end_persists_teardown_checkpoint_until_teardown_completes(
    _mock_sleep, tmp_path
):
    store = PatrolCycleCheckpointStore(tmp_path / "cp.db")
    engine = _make_engine(store)
    engine._check_init_returns = MagicMock(return_value=StepResult(success=True, exit_code=0))
    engine._run_lifecycle_steps = MagicMock(return_value=StepResult(success=True, exit_code=0))
    engine._run_patrol_loop = MagicMock(return_value=("timeout", ""))
    engine._footprint_families.add("power-cycle")

    observed_during_teardown: list[dict] = []

    def fake_teardown(teardown_def, *, disarm_only=False):
        row = store.get_for_recovery("211040")
        assert row is not None
        observed_during_teardown.append(dict(row.checkpoint))
        return StepResult(
            success=True,
            exit_code=0,
            metadata={"teardown_status": "SUCCESS", "disarm_results": {}},
        )

    engine._execute_teardown_best_effort = fake_teardown
    res = engine.execute(_lifecycle_pipeline())

    assert res.success is True
    assert len(observed_during_teardown) == 1
    assert observed_during_teardown[0]["phase"] == "teardown"
    assert observed_during_teardown[0]["termination_reason"] == "timeout"
    assert observed_during_teardown[0]["footprint_families"] == ["power-cycle"]
    assert store.get_for_recovery("211040") is None


def test_teardown_resume_from_checkpoint_skips_init_and_patrol(tmp_path):
    store = PatrolCycleCheckpointStore(tmp_path / "cp.db")
    store.save(
        "211040",
        {
            "phase": "teardown",
            "termination_reason": "timeout",
            "lifecycle_error": "",
            "footprint_families": ["power-cycle"],
            "disarm_results": {},
        },
    )
    executed_steps: list[tuple[str, str]] = []

    with (
        patch.object(PipelineEngine, "_check_init_returns", side_effect=AssertionError("must not run init return checks")),
        patch.object(PipelineEngine, "_run_patrol_loop", side_effect=AssertionError("must not re-enter patrol")),
        patch.object(PipelineEngine, "_teardown_lease_lost", return_value=False),
        patch.object(PipelineEngine, "_report_step_trace_mq", return_value=None),
    ):
        def fake_script(self, _ctx, step):
            executed_steps.append((step["step_id"], step["action"]))
            return StepResult(success=True, exit_code=0, output='{"success": true}')

        with patch.object(PipelineEngine, "_run_script_action", fake_script):
            out = execute_pipeline_run(
                _lifecycle_pipeline(),
                run_id=211040,
                device_serial="MOCK-SERIAL",
                adb=MagicMock(),
                api_url="",
                host_id="host-1",
                mq_producer=MagicMock(connected=True),
                patrol_cycle_checkpoint_store=store,
            )

    assert out["status"] == "FINISHED"
    assert executed_steps == [
        ("powercycle_disarm", "script:powercycle_disarm"),
        ("powercycle_finish", "script:powercycle_finish"),
    ]
    assert store.get_for_recovery("211040") is None


def test_teardown_resume_skips_succeeded_disarm_but_runs_collect(tmp_path):
    store = PatrolCycleCheckpointStore(tmp_path / "cp.db")
    executed_steps: list[str] = []

    with (
        patch.object(PipelineEngine, "_check_init_returns", side_effect=AssertionError("must not run init return checks")),
        patch.object(PipelineEngine, "_run_patrol_loop", side_effect=AssertionError("must not re-enter patrol")),
        patch.object(PipelineEngine, "_teardown_lease_lost", return_value=False),
        patch.object(PipelineEngine, "_report_step_trace_mq", return_value=None),
    ):
        def fake_script(self, _ctx, step):
            executed_steps.append(step["step_id"])
            return StepResult(success=True, exit_code=0)

        with patch.object(PipelineEngine, "_run_script_action", fake_script):
            out = execute_pipeline_run(
                _lifecycle_pipeline(),
                run_id=211040,
                device_serial="MOCK-SERIAL",
                adb=MagicMock(),
                api_url="",
                host_id="host-1",
                mq_producer=MagicMock(connected=True),
                patrol_cycle_checkpoint_store=store,
                teardown_resume={
                    "phase": "teardown",
                    "termination_reason": "timeout",
                    "footprint_families": ["power-cycle"],
                    "disarm_results": {
                        "power-cycle": {
                            "result": "executed_unverified",
                            "step_id": "powercycle_disarm",
                            "exit_code": 0,
                        }
                    },
                },
            )

    assert out["status"] == "FINISHED"
    assert executed_steps == ["powercycle_finish"]
    assert out["disarm_results"]["power-cycle"]["result"] == "executed_unverified"


def test_teardown_resume_retries_incomplete_disarm_step(tmp_path):
    store = PatrolCycleCheckpointStore(tmp_path / "cp.db")
    executed_steps: list[str] = []

    with (
        patch.object(PipelineEngine, "_check_init_returns", side_effect=AssertionError("must not run init return checks")),
        patch.object(PipelineEngine, "_run_patrol_loop", side_effect=AssertionError("must not re-enter patrol")),
        patch.object(PipelineEngine, "_teardown_lease_lost", return_value=False),
        patch.object(PipelineEngine, "_report_step_trace_mq", return_value=None),
    ):
        def fake_script(self, _ctx, step):
            executed_steps.append(step["step_id"])
            return StepResult(success=True, exit_code=0, output='{"success": true}')

        with patch.object(PipelineEngine, "_run_script_action", fake_script):
            out = execute_pipeline_run(
                _lifecycle_pipeline(),
                run_id=210996,
                device_serial="MOCK-SERIAL",
                adb=MagicMock(),
                api_url="",
                host_id="host-1",
                mq_producer=MagicMock(connected=True),
                patrol_cycle_checkpoint_store=store,
                teardown_resume={
                    "phase": "teardown",
                    "termination_reason": "timeout",
                    "footprint_families": ["power-cycle"],
                    "disarm_results": {
                        "power-cycle": {
                            "result": "incomplete",
                            "step_id": "powercycle_disarm",
                            "exit_code": 124,
                        }
                    },
                },
            )

    assert out["status"] == "FINISHED"
    assert executed_steps == ["powercycle_disarm", "powercycle_finish"]


def test_disarm_step_clears_coordinator_evidence_before_collect_step(monkeypatch):
    coord = HostRunCoordinator("http://test-only", "host-1", "inst-1")
    coord.disarm_protocol = 1
    engine = PipelineEngine(
        SimpleNamespace(adb_path="stub"),
        "SERIAL-1",
        211040,
        nfs_root="",
        api_url="http://test-only",
        fencing_token="1957:12",
        coordinator=coord,
        operation_scheduler=OperationScheduler(max_concurrent=1),
        device_id=1957,
    )
    engine._footprint_families = {"power-cycle"}
    monkeypatch.setattr(engine, "_report_step_trace_mq", lambda *a, **k: None)
    monkeypatch.setattr(
        pe_mod.requests,
        "post",
        lambda *a, **k: SimpleNamespace(status_code=200),
    )

    seen_evidence_when_collect_runs: list[object] = []

    def fake_action(_ctx, step):
        jv = coord.register_job(211040)
        if step["step_id"] == "powercycle_finish":
            with jv._lock:
                seen_evidence_when_collect_runs.append(jv.disarm_evidence)
        return StepResult(success=True, exit_code=0)

    monkeypatch.setattr(engine, "_run_script_action", fake_action)
    result = engine._execute_teardown_best_effort(
        _lifecycle_pipeline()["lifecycle"]["teardown"]
    )

    assert result.success is True
    assert seen_evidence_when_collect_runs == [None]
