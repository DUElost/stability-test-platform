# -*- coding: utf-8 -*-
"""Standard Android Monkey stress test — Pipeline Action."""

from typing import Any, Dict

from ..pipeline_engine import PipelineAction, StepContext, StepResult


class MonkeyAction(PipelineAction):
    """Send random UI events to the device via the built-in ``monkey`` command."""

    TOOL_CATEGORY = "MONKEY"
    TOOL_DESCRIPTION = "Standard Android Monkey random event stress test."

    @classmethod
    def get_default_params(cls) -> Dict[str, Any]:
        return {
            "packages": [],
            "event_count": 10000,
            "throttle": 100,
            "seed": None,
        }

    def run(self, ctx: StepContext) -> StepResult:
        packages = ctx.params.get("packages") or []
        event_count = int(ctx.params.get("event_count", 10000))
        throttle = int(ctx.params.get("throttle", 100))
        seed = ctx.params.get("seed")

        cmd = ["monkey"]
        for pkg in packages:
            cmd += ["-p", pkg]
        cmd += ["--throttle", str(throttle)]
        if seed is not None:
            cmd += ["-s", str(seed)]
        cmd += [str(event_count)]

        timeout = max(event_count // 10 + 300, 600)
        ctx.logger.info(f"执行 monkey: events={event_count}, throttle={throttle}, pkgs={packages}")
        result = ctx.adb.shell(ctx.serial, cmd, timeout=timeout)
        exit_code = getattr(result, "returncode", 0)
        stdout = getattr(result, "stdout", "") or ""

        return StepResult(
            success=exit_code == 0,
            exit_code=exit_code,
            error_message=stdout[-2000:] if exit_code != 0 else "",
        )
