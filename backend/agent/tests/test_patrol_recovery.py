"""Patrol JOB_NOT_RUNNING → one-shot recovery/sync trigger tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.agent.patrol_recovery import build_patrol_job_not_running_handler


class TestPatrolJobNotRunningRecovery:
    def test_triggers_recovery_once_per_job(self):
        execute_actions = MagicMock()
        local_db = MagicMock()

        handler = build_patrol_job_not_running_handler(
            api_url="http://test",
            host_id="host-1",
            agent_instance_id="inst-1",
            boot_id="boot-1",
            local_db=local_db,
            execute_actions=execute_actions,
        )

        with patch("backend.agent.main.run_recovery_sync_if_needed") as mock_sync:
            handler(42)
            handler(42)
            handler(99)

        assert mock_sync.call_count == 2
        mock_sync.assert_any_call(
            local_db=local_db,
            api_url="http://test",
            host_id="host-1",
            agent_instance_id="inst-1",
            boot_id="boot-1",
            execute_actions=execute_actions,
        )

    def test_pipeline_runner_wires_callback_to_uploader(self):
        from backend.agent.pipeline_runner import execute_pipeline_run

        callback = MagicMock()
        with patch("backend.agent.pipeline_runner.PatrolHeartbeatUploader") as UploaderCls, patch(
            "backend.agent.pipeline_runner.PipelineEngine"
        ) as EngineCls:
            engine = EngineCls.return_value
            engine.execute.return_value = MagicMock(
                success=True,
                exit_code=0,
                error_message=None,
                artifact=None,
                metadata={},
            )
            execute_pipeline_run(
                {"lifecycle": {"init": [], "patrol": {"interval_seconds": 60, "steps": []}, "teardown": []}},
                run_id=7,
                device_serial="dev",
                adb=MagicMock(),
                api_url="http://test",
                host_id="host-1",
                on_job_not_running_recovery=callback,
            )

        UploaderCls.assert_called_once()
        assert UploaderCls.call_args.kwargs["on_job_not_running"] is callback
