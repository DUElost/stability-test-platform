# -*- coding: utf-8 -*-
"""GPU stress test — Pipeline Action."""

import time
from typing import Any, Dict

from ..pipeline_engine import PipelineAction, StepContext, StepResult


class GpuStressAction(PipelineAction):
    """Install a GPU benchmark APK and loop-launch the test Activity."""

    TOOL_CATEGORY = "GPU"
    TOOL_DESCRIPTION = "GPU stress cycle test using Antutu or similar benchmark."

    @classmethod
    def get_default_params(cls) -> Dict[str, Any]:
        return {
            "apk_path": "",
            "activity": "com.antutu.ABenchMark/.ABenchMarkStart",
            "loops": 3,
            "interval": 120,
        }

    def run(self, ctx: StepContext) -> StepResult:
        apk_path = ctx.params.get("apk_path", "")
        activity = ctx.params.get("activity", "com.antutu.ABenchMark/.ABenchMarkStart")
        loops = int(ctx.params.get("loops", 3))
        interval = int(ctx.params.get("interval", 120))

        if apk_path:
            ctx.logger.info(f"安装 GPU test APK: {apk_path}")
            ctx.adb.install(ctx.serial, apk_path)

        summaries = []
        last_code = 0

        for idx in range(loops):
            ctx.logger.info(f"GPU loop {idx + 1}/{loops}")
            result = ctx.adb.shell(ctx.serial, ["am", "start", "-n", activity])
            last_code = getattr(result, "returncode", 0)
            summaries.append(f"loop={idx + 1}, exit={last_code}")
            if idx < loops - 1:
                time.sleep(interval)

        return StepResult(
            success=last_code == 0,
            exit_code=last_code,
            error_message="" if last_code == 0 else "; ".join(summaries),
            metrics={"loops_run": loops, "last_exit_code": last_code},
        )
