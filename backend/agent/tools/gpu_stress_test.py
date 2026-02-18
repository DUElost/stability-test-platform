# -*- coding: utf-8 -*-
"""
GPU stress test.
Installs a GPU benchmark APK and loop-launches the test Activity.
"""

import time
from typing import Any, Dict

from ..test_framework import BaseTestCase, TestResult
from ..test_stages import MINIMAL_STAGES


class GpuStressTest(BaseTestCase):
    """GPU stress cycle test using Antutu or similar benchmark."""

    TEST_TYPE = "GPU"
    STAGES = MINIMAL_STAGES

    def get_default_params(self) -> Dict[str, Any]:
        return {
            "apk_path": "",
            "activity": "com.antutu.ABenchMark/.ABenchMarkStart",
            "loops": 3,
            "interval": 120,
        }

    def setup(self, serial: str, params: Dict[str, Any]) -> None:
        super().setup(serial, params)
        apk_path = params.get("apk_path")
        if apk_path:
            self.adb.install(serial, apk_path)
            self._log(f"GPU test APK 已安装: {apk_path}")

    def execute(self, serial: str, params: Dict[str, Any]) -> TestResult:
        activity = params.get("activity", "com.antutu.ABenchMark/.ABenchMarkStart")
        loops = int(params.get("loops", 3))
        interval = int(params.get("interval", 120))

        summaries = []
        last_code = 0

        for idx in range(loops):
            self._log(f"GPU loop {idx + 1}/{loops}")
            self.set_progress(
                35 + int((idx / max(loops, 1)) * 50),
                f"GPU loop {idx + 1}/{loops}",
            )
            result = self._run_shell(serial, ["am", "start", "-n", activity])
            last_code = result.returncode
            summaries.append(f"loop={idx + 1}, exit={result.returncode}")
            if idx < loops - 1:
                time.sleep(interval)

        status = "FINISHED" if last_code == 0 else "FAILED"
        return TestResult(
            status=status,
            exit_code=last_code,
            log_summary="; ".join(summaries)[-2000:],
        )
