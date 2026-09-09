"""Patrol JOB_NOT_RUNNING → one-shot recovery/sync trigger tests."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.agent.patrol_recovery import build_patrol_job_not_running_handler


def _purge_toplevel_agent() -> None:
    """Drop any freshly imported top-level ``agent`` modules so tests do not
    contaminate one another (the real backends live under ``backend.agent``)."""
    for name in [m for m in sys.modules if m == "agent" or m.startswith("agent.")]:
        del sys.modules[name]


def _install_agent_source(tmp_path: Path) -> Path:
    """Copy the agent package so it is importable as top-level ``agent``
    (production layout: ``install_agent.sh`` deploys ``agent/`` without the
    ``backend`` top-level package)."""
    root = tmp_path / "install"
    pkg = root / "agent"
    pkg.mkdir(parents=True)
    shutil.copy(
        Path(__file__).resolve().parents[1] / "patrol_recovery.py",
        pkg / "patrol_recovery.py",
    )
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    return root


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

    def test_pipeline_runner_passes_watcher_capability_to_engine(self):
        from backend.agent.pipeline_runner import execute_pipeline_run

        with patch("backend.agent.pipeline_runner.PatrolHeartbeatUploader"), patch(
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
                watcher_capability="inotifyd_root",
            )

        assert EngineCls.call_args.kwargs["watcher_capability"] == "inotifyd_root"

    def test_production_layout_without_backend_package_triggers_recovery(self, tmp_path):
        """R07-F06 (#1008): install_agent.sh deploys top-level ``agent`` only;
        patrol_recovery must resolve its recovery callback without the
        ``backend`` top-level package on sys.path."""
        import importlib

        captured = MagicMock()
        root = _install_agent_source(tmp_path)
        (root / "agent" / "main.py").write_text(
            "def run_recovery_sync_if_needed(**kwargs):\n"
            "    return None\n",
            encoding="utf-8",
        )

        with patch.object(sys, "path", [str(root), *sys.path]):
            try:
                _purge_toplevel_agent()
                mod = importlib.import_module("agent.patrol_recovery")
                main_mod = importlib.import_module("agent.main")
                handler = mod.build_patrol_job_not_running_handler(
                    api_url="http://prod",
                    host_id="h",
                    agent_instance_id="a",
                    boot_id="b",
                    local_db=MagicMock(),
                    execute_actions=MagicMock(),
                )
                with patch.object(main_mod, "run_recovery_sync_if_needed", captured):
                    handler(7)
            finally:
                _purge_toplevel_agent()

        assert captured.call_count == 1
        assert captured.call_args.kwargs["host_id"] == "h"
