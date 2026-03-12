# -*- coding: utf-8 -*-
"""MTBF stability regression test — Pipeline Action."""

import os
from typing import Any, Dict

from ..pipeline_engine import PipelineAction, StepContext, StepResult


class MtbfAction(PipelineAction):
    """MTBF stability regression test: push resources, install APK, run am instrument."""

    TOOL_CATEGORY = "MTBF"
    TOOL_DESCRIPTION = "MTBF stability regression test via am instrument."

    @classmethod
    def get_default_params(cls) -> Dict[str, Any]:
        return {
            "resource_dir": "",
            "remote_dir": "/sdcard/mtbf",
            "apk_path": "",
            "runner": "com.transsion.stresstest.test/androidx.test.runner.AndroidJUnitRunner",
            "instrument_args": {},
            "timeout": 86400,
        }

    def run(self, ctx: StepContext) -> StepResult:
        resource_dir = ctx.params.get("resource_dir", "")
        remote_dir = ctx.params.get("remote_dir", "/sdcard/mtbf")
        apk_path = ctx.params.get("apk_path", "")
        runner = ctx.params.get("runner", "com.transsion.stresstest.test/androidx.test.runner.AndroidJUnitRunner")
        instrument_args = ctx.params.get("instrument_args") or {}
        timeout = int(ctx.params.get("timeout", 86400))

        if resource_dir and os.path.exists(resource_dir):
            target = os.path.join(remote_dir, os.path.basename(resource_dir))
            ctx.logger.info(f"推送资源: {resource_dir} → {target}")
            ctx.adb.push(ctx.serial, resource_dir, target)

        if apk_path and os.path.exists(apk_path):
            ctx.logger.info(f"安装 APK: {apk_path}")
            ctx.adb.install(ctx.serial, apk_path)

        cmd = ["am", "instrument", "-w"]
        for key, value in instrument_args.items():
            cmd += ["-e", str(key), str(value)]
        cmd.append(runner)

        ctx.logger.info(f"执行 am instrument: runner={runner}")
        result = ctx.adb.shell(ctx.serial, cmd, timeout=timeout)
        exit_code = getattr(result, "returncode", 0)
        stdout = getattr(result, "stdout", "") or ""

        return StepResult(
            success=True,
            exit_code=exit_code,
            metrics={"runner": runner, "output_tail": stdout[-500:]},
        )
