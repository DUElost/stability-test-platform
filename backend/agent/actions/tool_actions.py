"""Tool adapter action: run_tool_script — execute a registered Tool as a pipeline step."""

import importlib
import importlib.util
import logging
from backend.agent.pipeline_engine import StepContext, StepResult

logger = logging.getLogger(__name__)


def run_tool_script(ctx: StepContext) -> StepResult:
    """Load and execute a registered Tool class from tool_snapshot as a single step."""
    tool_snapshot = ctx.params.get("tool_snapshot", {})
    script_path = tool_snapshot.get("script_path", ctx.params.get("script_path", ""))
    script_class = tool_snapshot.get("script_class", ctx.params.get("script_class", ""))

    if not script_path or not script_class:
        return StepResult(success=False, exit_code=1, error_message="tool_snapshot missing script_path or script_class")

    try:
        # Dynamic import of the tool module
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
        instance = tool_class(ctx.adb, None)  # execution_context handled differently in pipeline mode

        # Merge default params with step params
        default_params = tool_snapshot.get("default_params", {})
        merged_params = {**default_params, **ctx.params}

        result = instance.run(ctx.serial, merged_params)

        # Map test result to step result
        success = result.status == "PASSED" if hasattr(result, "status") else bool(result)
        return StepResult(
            success=success,
            exit_code=getattr(result, "exit_code", 0) if success else 1,
            error_message=getattr(result, "error_message", "") if not success else "",
        )

    except Exception as e:
        logger.error(f"run_tool_script failed: {e}", exc_info=True)
        return StepResult(success=False, exit_code=1, error_message=str(e))
