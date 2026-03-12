# -*- coding: utf-8 -*-
"""DDR memory stress test — Pipeline Action."""

from typing import Any, Dict

from ..pipeline_engine import PipelineAction, StepContext, StepResult


class DdrAction(PipelineAction):
    """DDR memory stress test using memtester. Requires root."""

    TOOL_CATEGORY = "DDR"
    TOOL_DESCRIPTION = "DDR memory stress test using memtester (root required)."

    @classmethod
    def get_default_params(cls) -> Dict[str, Any]:
        return {
            "memtester_path": "",
            "remote_path": "/data/local/tmp/memtester",
            "mem_size_mb": 512,
            "loops": 1,
        }

    def run(self, ctx: StepContext) -> StepResult:
        memtester_path = ctx.params.get("memtester_path", "")
        remote_path = ctx.params.get("remote_path", "/data/local/tmp/memtester")
        mem_size_mb = int(ctx.params.get("mem_size_mb", 512))
        loops = int(ctx.params.get("loops", 1))

        # Verify root
        id_result = ctx.adb.shell(ctx.serial, ["id", "-u"])
        uid = (getattr(id_result, "stdout", "") or "").strip()
        if uid != "0":
            return StepResult(success=False, exit_code=1, error_message="DDR test requires root access")

        if memtester_path:
            ctx.logger.info(f"推送 memtester: {memtester_path} → {remote_path}")
            ctx.adb.push(ctx.serial, memtester_path, remote_path)
            ctx.adb.shell(ctx.serial, ["chmod", "755", remote_path])

        timeout = mem_size_mb * loops * 10
        ctx.logger.info(f"执行 memtester: size={mem_size_mb}MB, loops={loops}, timeout={timeout}s")
        result = ctx.adb.shell(ctx.serial, [remote_path, str(mem_size_mb), str(loops)], timeout=timeout)
        exit_code = getattr(result, "returncode", 0)
        stdout = getattr(result, "stdout", "") or ""

        return StepResult(
            success=exit_code == 0,
            exit_code=exit_code,
            error_message="" if exit_code == 0 else f"memtester failed (exit={exit_code})",
            metrics={"mem_size_mb": mem_size_mb, "loops": loops, "output_tail": stdout[-500:]},
        )
