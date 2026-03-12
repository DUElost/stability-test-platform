# -*- coding: utf-8 -*-
"""Standby / battery drain test — Pipeline Action."""

import time
from typing import Any, Dict

from ..pipeline_engine import PipelineAction, StepContext, StepResult


class StandbyAction(PipelineAction):
    """Play a video, turn off the screen, and monitor standby duration."""

    TOOL_CATEGORY = "STANDBY"
    TOOL_DESCRIPTION = "Standby / battery drain scenario test."

    @classmethod
    def get_default_params(cls) -> Dict[str, Any]:
        return {
            "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "standby_seconds": 1800,
            "screen_off": True,
        }

    def run(self, ctx: StepContext) -> StepResult:
        video_url = ctx.params.get("video_url", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        standby_seconds = int(ctx.params.get("standby_seconds", 1800))
        screen_off = bool(ctx.params.get("screen_off", True))

        ctx.logger.info(f"启动视频: {video_url}")
        ctx.adb.shell(ctx.serial, [
            "am", "start", "-a", "android.intent.action.VIEW", "-d", video_url,
        ])

        if screen_off:
            ctx.adb.shell(ctx.serial, ["input", "keyevent", "26"])
            ctx.logger.info("已关闭屏幕")

        elapsed = 0
        chunk = min(standby_seconds // 10, 60) or 1
        while elapsed < standby_seconds:
            sleep_time = min(chunk, standby_seconds - elapsed)
            time.sleep(sleep_time)
            elapsed += sleep_time
            progress_pct = int(elapsed / standby_seconds * 100)
            ctx.logger.info(f"待机进度: {elapsed}/{standby_seconds}s ({progress_pct}%)")

        return StepResult(
            success=True,
            exit_code=0,
            metrics={"standby_seconds": standby_seconds},
        )
