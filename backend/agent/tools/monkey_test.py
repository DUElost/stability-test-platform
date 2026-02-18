# -*- coding: utf-8 -*-
"""
Standard Android Monkey test.
Sends random UI events to the device via the built-in monkey command.
"""

from typing import Any, Dict

from ..test_framework import BaseTestCase, TestResult
from ..test_stages import MINIMAL_STAGES


class MonkeyTest(BaseTestCase):
    """Standard Android Monkey random event stress test."""

    TEST_TYPE = "MONKEY"
    STAGES = MINIMAL_STAGES  # PRECHECK, RUN, TEARDOWN

    def get_default_params(self) -> Dict[str, Any]:
        return {
            "packages": [],
            "event_count": 10000,
            "throttle": 100,
            "seed": None,
        }

    def execute(self, serial: str, params: Dict[str, Any]) -> TestResult:
        packages = params.get("packages") or []
        event_count = int(params.get("event_count", 10000))
        throttle = int(params.get("throttle", 100))
        seed = params.get("seed")

        cmd = ["monkey"]
        for pkg in packages:
            cmd += ["-p", pkg]
        cmd += ["--throttle", str(throttle)]
        if seed is not None:
            cmd += ["-s", str(seed)]
        cmd += [str(event_count)]

        self._log(f"执行 monkey: events={event_count}, throttle={throttle}")
        result = self._run_shell(serial, cmd, timeout=max(event_count // 10 + 300, 600))
        exit_code = result.returncode

        return TestResult(
            status="FINISHED" if exit_code == 0 else "FAILED",
            exit_code=exit_code,
            log_summary=(result.stdout or "")[-2000:],
        )
