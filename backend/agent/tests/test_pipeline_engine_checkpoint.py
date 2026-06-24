"""PipelineEngine patrol checkpoint persistence tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.agent.pipeline_engine import PipelineEngine, StepResult
from backend.agent.registry.patrol_checkpoint_store import (
    PatrolCycleCheckpointStore,
    PatrolCycleCheckpointStoreRecoverableError,
)


def _patrol_pipeline(*, interval: int = 1) -> dict:
    return {
        "lifecycle": {
            "timeout_seconds": 0,
            "init": [
                {
                    "step_id": "init_step",
                    "action": "script:check_device",
                    "version": "1.0.0",
                    "params": {},
                }
            ],
            "patrol": {
                "interval_seconds": interval,
                "steps": [
                    {
                        "step_id": "patrol_a",
                        "action": "script:check_device",
                        "version": "1.0.0",
                        "params": {},
                    }
                ],
            },
            "teardown": [],
        }
    }


def _make_engine(store: PatrolCycleCheckpointStore | None = None) -> PipelineEngine:
    uploader = MagicMock()
    uploader.send.return_value = {"manual_action": None}
    engine = PipelineEngine(
        adb=MagicMock(),
        serial="MOCK",
        run_id=42,
        log_dir="/tmp/logs",
        mq_producer=MagicMock(connected=True),
        api_url=None,
        is_aborted=lambda: False,
        patrol_heartbeat_uploader=uploader,
        patrol_cycle_checkpoint_store=store,
    )
    engine._verify_device_lease = lambda: None
    engine._archive_logs = lambda: None
    engine._run_lifecycle_steps = MagicMock(
        return_value=StepResult(success=True, exit_code=0)
    )
    engine._run_patrol_cycle_steps = MagicMock(return_value=(1, 0, None))
    return engine


@patch("backend.agent.pipeline_engine.time.sleep", return_value=None)
def test_patrol_loop_persists_checkpoint_after_cycle(mock_sleep, tmp_path):
    store = PatrolCycleCheckpointStore(tmp_path / "cp.db")
    engine = _make_engine(store)

    call_count = {"n": 0}
    original_cycle = engine._run_patrol_cycle_steps

    def stop_after_one(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            engine._canceled = True
        return original_cycle(*args, **kwargs)

    engine._run_patrol_cycle_steps = stop_after_one

    engine.execute(_patrol_pipeline())

    row = store.get_for_recovery("42")
    assert row is None  # dropped on patrol end


@patch("backend.agent.pipeline_engine.time.sleep", return_value=None)
def test_patrol_loop_saves_checkpoint_mid_run(mock_sleep, tmp_path):
    store = PatrolCycleCheckpointStore(tmp_path / "cp.db")
    engine = _make_engine(store)
    saved_cycles: list[int] = []

    original_persist = engine._persist_patrol_cycle_checkpoint

    def track_save(payload):
        saved_cycles.append(payload["cycle"])
        if len(saved_cycles) >= 1:
            engine._canceled = True
        original_persist(payload)

    engine._persist_patrol_cycle_checkpoint = track_save

    engine.execute(_patrol_pipeline())

    assert saved_cycles == [1]


@patch("backend.agent.pipeline_engine.time.sleep", return_value=None)
def test_resume_skips_init(mock_sleep, tmp_path):
    store = PatrolCycleCheckpointStore(tmp_path / "cp.db")
    engine = _make_engine(store)
    engine.set_patrol_cycle_resume({"cycle": 5, "failure_streak": 2})
    engine._canceled = True

    engine.execute(_patrol_pipeline())

    engine._run_lifecycle_steps.assert_not_called()


@patch("backend.agent.pipeline_engine.time.sleep", return_value=None)
def test_resume_restores_cycle_counter(mock_sleep, tmp_path):
    store = PatrolCycleCheckpointStore(tmp_path / "cp.db")
    engine = _make_engine(store)
    engine.set_patrol_cycle_resume({"cycle": 3, "failure_streak": 1})

    seen_iterations: list[int] = []

    original = engine._run_patrol_cycle_steps

    def capture(*args, **kwargs):
        seen_iterations.append(engine._run_id)
        engine._canceled = True
        return (1, 0, None)

    engine._run_patrol_cycle_steps = capture

    with patch.object(engine, "_run_patrol_loop", wraps=engine._run_patrol_loop) as spy:
        engine.execute(_patrol_pipeline())
        assert spy.call_args.kwargs["resume"]["cycle"] == 3


def test_persist_checkpoint_swallows_recoverable_error(tmp_path, monkeypatch):
    store = PatrolCycleCheckpointStore(tmp_path / "cp.db")
    store.initialize()
    engine = _make_engine(store)

    def fail_save(_job_id, _payload):
        raise PatrolCycleCheckpointStoreRecoverableError("locked")

    monkeypatch.setattr(store, "save", fail_save)

    engine._persist_patrol_cycle_checkpoint({"cycle": 1, "failure_streak": 0})
