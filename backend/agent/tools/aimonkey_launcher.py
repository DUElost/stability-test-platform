# -*- coding: utf-8 -*-
"""AIMonkey 20260317 launcher action.

Runs the existing MonkeyTest.py launcher for the device currently assigned to
the platform Job. Long-running log monitoring stays in JobSession watcher.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any, Dict

try:
    from ..pipeline_engine import PipelineAction, StepContext, StepResult
except ImportError:  # Loaded directly from script_path by run_tool_script.
    try:
        from agent.pipeline_engine import PipelineAction, StepContext, StepResult
    except ModuleNotFoundError:  # Test/backend package layout.
        from backend.agent.pipeline_engine import PipelineAction, StepContext, StepResult


class AIMonkeyLauncherAction(PipelineAction):
    """Start AIMonkey through the bundled AIMonkeyTest_20260317 launcher."""

    TOOL_CATEGORY = "Monkey"
    TOOL_DESCRIPTION = "AIMonkey 20260317 启动器：调用 MonkeyTest.py 启动当前设备测试。"

    @classmethod
    def get_default_params(cls) -> Dict[str, Any]:
        return {
            "launcher_dir": "",
            "launcher_script": "MonkeyTest.py",
            "need_nohup": True,
            "push_resources": True,
            "sleep_mode": False,
            "blacklist": True,
        }

    def run(self, ctx: StepContext) -> StepResult:
        params = {**self.get_default_params(), **(ctx.params or {})}
        launcher_dir = Path(str(params.get("launcher_dir") or self._default_launcher_dir())).resolve()
        launcher_script = str(params.get("launcher_script") or "MonkeyTest.py")
        script_path = launcher_dir / launcher_script

        if not script_path.exists():
            return StepResult(
                success=False,
                exit_code=1,
                error_message=f"MonkeyTest.py not found: {script_path}",
            )

        serial = str(ctx.serial or "").strip()
        if not serial:
            return StepResult(success=False, exit_code=1, error_message="device serial is required")

        old_cwd = Path.cwd()
        inserted_path = str(launcher_dir)
        inserted = False
        try:
            if inserted_path not in sys.path:
                sys.path.insert(0, inserted_path)
                inserted = True
            os.chdir(launcher_dir)

            module = self._load_launcher_module(script_path)
            monkey_test_cls = getattr(module, "MonkeyTest")
            runner = monkey_test_cls(
                bool(params.get("need_nohup", True)),
                bool(params.get("push_resources", True)),
                bool(params.get("sleep_mode", False)),
                bool(params.get("blacklist", True)),
            )

            ctx.logger.info(f"AIMonkey launcher starting serial={serial} dir={launcher_dir}")
            runner.startTest(serial)
            return StepResult(
                success=True,
                metrics={
                    "serial": serial,
                    "launcher_dir": str(launcher_dir),
                    "launcher_script": launcher_script,
                },
            )
        except Exception as exc:
            return StepResult(success=False, exit_code=1, error_message=str(exc))
        finally:
            os.chdir(old_cwd)
            if inserted:
                try:
                    sys.path.remove(inserted_path)
                except ValueError:
                    pass

    @staticmethod
    def _default_launcher_dir() -> Path:
        return Path(__file__).resolve().parent / "AIMonkeyTest_20260317"

    @staticmethod
    def _load_launcher_module(script_path: Path):
        module_name = f"_stp_aimonkey_launcher_{abs(hash(str(script_path)))}"
        spec = importlib.util.spec_from_file_location(module_name, script_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load launcher module: {script_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
