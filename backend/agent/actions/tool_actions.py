"""Tool adapter action: run_tool_script — execute a script-based Tool as a pipeline step."""

import importlib
import importlib.util
import json
import logging
import os

try:
    from .. import config
    from ..pipeline_engine import StepContext, StepResult
except ModuleNotFoundError:  # pragma: no cover
    from agent import config
    from agent.pipeline_engine import StepContext, StepResult

logger = logging.getLogger(__name__)


def _convert_windows_path(script_path: str) -> str:
    """将 Windows 路径转换为 Linux 路径（如果是 WSL 环境）。"""
    if not script_path:
        return script_path

    if len(script_path) >= 2 and script_path[1] == ":":
        drive_letter = script_path[0].lower()
        linux_path = f"/mnt/{drive_letter}{script_path[2:].replace(chr(92), '/')}"
        if os.path.exists(linux_path):
            return linux_path
    return script_path


def run_tool_script(ctx: StepContext) -> StepResult:
    """Load and execute a Tool class as a single step using explicit script_path/script_class."""
    script_path = ctx.params.get("script_path", "")
    script_class = ctx.params.get("script_class", "")

    if not script_path or not script_class:
        return StepResult(success=False, exit_code=1, error_message="script_path and script_class are required")

    try:
        # Dynamic import of the tool module
        script_path = _convert_windows_path(script_path)
        spec = importlib.util.spec_from_file_location("dynamic_tool", script_path)
        if not spec or not spec.loader:
            return StepResult(success=False, exit_code=1, error_message=f"Cannot load module from {script_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Get the test class
        tool_class = getattr(module, script_class, None)
        if tool_class is None:
            return StepResult(success=False, exit_code=1, error_message=f"Class {script_class} not found in {script_path}")

        # Instantiate and run
        api_url = os.getenv("API_URL", "")
        host_id = int(os.getenv("HOST_ID", "0") or 0)
        log_dir = config.get_run_log_dir(ctx.run_id) if ctx.run_id else ""
        instance = tool_class(
            adb_wrapper=ctx.adb,
            api_url=api_url,
            run_id=ctx.run_id,
            host_id=host_id,
            device_serial=ctx.serial,
            log_dir=log_dir,
        )

        # Merge default params with step params
        default_params = ctx.params.get("default_params", {})
        if default_params is None:
            default_params = {}
        if isinstance(default_params, str):
            if not default_params.strip():
                default_params = {}
            else:
                try:
                    default_params = json.loads(default_params)
                except Exception:
                    return StepResult(success=False, exit_code=1, error_message="default_params must be valid JSON")
        if not isinstance(default_params, dict):
            return StepResult(success=False, exit_code=1, error_message="default_params must be an object")

        merged_params = {**default_params, **ctx.params}

        result = instance.run(ctx.serial, merged_params)

        # Map test result to step result
        if hasattr(result, "status"):
            success = getattr(result, "status") in ("FINISHED", "PASSED")
        else:
            success = bool(result)
        return StepResult(
            success=success,
            exit_code=getattr(result, "exit_code", 0) if success else 1,
            error_message=getattr(result, "error_message", "") if not success else "",
        )

    except Exception as e:
        logger.error(f"run_tool_script failed: {e}", exc_info=True)
        return StepResult(success=False, exit_code=1, error_message=str(e))
