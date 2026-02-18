# -*- coding: utf-8 -*-
"""
Standby / battery drain test.
Plays a video, turns off the screen, and monitors standby duration.
"""

import time
from typing import Any, Dict

from ..test_framework import BaseTestCase, TestResult
from ..test_stages import MINIMAL_STAGES


class StandbyTest(BaseTestCase):
    """Standby / battery drain scenario test."""

    TEST_TYPE = "STANDBY"
    STAGES = MINIMAL_STAGES

    def get_default_params(self) -> Dict[str, Any]:
        return {
            "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "standby_seconds": 1800,
            "screen_off": True,
        }

    def execute(self, serial: str, params: Dict[str, Any]) -> TestResult:
        video_url = params.get(
            "video_url", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        standby_seconds = int(params.get("standby_seconds", 1800))
        screen_off = bool(params.get("screen_off", True))

        self._log(f"启动视频: {video_url}")
        self._run_shell(serial, [
            "am", "start", "-a", "android.intent.action.VIEW",
            "-d", video_url,
        ])

        if screen_off:
            self._run_shell(serial, ["input", "keyevent", "26"])
            self._log("已关闭屏幕")

        # Report progress periodically during standby
        elapsed = 0
        chunk = min(standby_seconds // 10, 60) or 1
        while elapsed < standby_seconds:
            sleep_time = min(chunk, standby_seconds - elapsed)
            time.sleep(sleep_time)
            elapsed += sleep_time
            progress = 35 + int((elapsed / standby_seconds) * 55)
            self.set_progress(progress, f"待机 {elapsed}/{standby_seconds}s")

        return TestResult(
            status="FINISHED",
            exit_code=0,
            log_summary=f"standby {standby_seconds}s",
        )
