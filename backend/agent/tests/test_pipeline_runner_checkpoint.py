"""Pipeline runner checkpoint resume wiring tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.agent.pipeline_engine import StepResult
from backend.agent.pipeline_runner import execute_pipeline_run
from backend.agent.registry.patrol_checkpoint_store import PatrolCycleCheckpointStore


@patch("backend.agent.pipeline_runner.PipelineEngine")
@patch("backend.agent.pipeline_runner.PatrolHeartbeatUploader")
def test_execute_pipeline_run_applies_checkpoint_resume(
    _uploader_cls, engine_cls, tmp_path
):
    store = PatrolCycleCheckpointStore(tmp_path / "cp.db")
    store.save("99", {"cycle": 4, "failure_streak": 0})

    engine = MagicMock()
    engine.execute.return_value = StepResult(success=True, exit_code=0)
    engine_cls.return_value = engine

    execute_pipeline_run(
        {"lifecycle": {"init": [], "teardown": [], "timeout_seconds": 0}},
        99,
        "SERIAL",
        MagicMock(),
        "http://api",
        host_id="host-1",
        patrol_cycle_checkpoint_store=store,
    )

    engine.set_patrol_cycle_resume.assert_called_once_with(
        {"cycle": 4, "failure_streak": 0}
    )
