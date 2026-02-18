# -*- coding: utf-8 -*-
"""
MTBF (Mean Time Between Failures) stability regression test.
Pushes resources, installs APK, then runs am instrument.
"""

import os
from typing import Any, Dict

from ..test_framework import BaseTestCase, TestResult
from ..test_stages import STANDARD_STAGES


class MtbfTest(BaseTestCase):
    """MTBF stability regression test."""

    TEST_TYPE = "MTBF"
    STAGES = STANDARD_STAGES

    def get_default_params(self) -> Dict[str, Any]:
        return {
            "resource_dir": "",
            "remote_dir": "/sdcard/mtbf",
            "apk_path": "",
            "runner": "com.transsion.stresstest.test/androidx.test.runner.AndroidJUnitRunner",
            "instrument_args": {},
        }

    def setup(self, serial: str, params: Dict[str, Any]) -> None:
        super().setup(serial, params)

        resource_dir = params.get("resource_dir")
        remote_dir = params.get("remote_dir", "/sdcard/mtbf")
        apk_path = params.get("apk_path")

        if resource_dir and os.path.exists(resource_dir):
            target = os.path.join(remote_dir, os.path.basename(resource_dir))
            self._push_file(serial, resource_dir, target)
            self._log(f"资源已推送到 {target}")

        if apk_path and os.path.exists(apk_path):
            self.adb.install(serial, apk_path)
            self._log(f"APK 已安装: {apk_path}")

    def execute(self, serial: str, params: Dict[str, Any]) -> TestResult:
        runner = params.get(
            "runner",
            "com.transsion.stresstest.test/androidx.test.runner.AndroidJUnitRunner",
        )
        instrument_args = params.get("instrument_args") or {}

        cmd = ["am", "instrument", "-w"]
        for key, value in instrument_args.items():
            cmd += ["-e", str(key), str(value)]
        cmd.append(runner)

        self._log(f"执行 am instrument: runner={runner}")
        result = self._run_shell(serial, cmd, timeout=86400)

        return TestResult(
            status="FINISHED",
            exit_code=result.returncode,
            log_summary=(result.stdout or "")[-2000:],
        )
