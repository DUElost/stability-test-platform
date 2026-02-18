# -*- coding: utf-8 -*-
"""
DDR memory stress test.
Requires root. Pushes memtester binary and runs it on the device.
"""

from typing import Any, Dict

from ..test_framework import BaseTestCase, TestResult
from ..test_stages import MINIMAL_STAGES


class DdrTest(BaseTestCase):
    """DDR memory stress test using memtester."""

    TEST_TYPE = "DDR"
    STAGES = MINIMAL_STAGES

    def get_default_params(self) -> Dict[str, Any]:
        return {
            "memtester_path": "",
            "remote_path": "/data/local/tmp/memtester",
            "mem_size_mb": 512,
            "loops": 1,
        }

    def setup(self, serial: str, params: Dict[str, Any]) -> None:
        # DDR requires root
        if not self._ensure_root(serial):
            raise RuntimeError("DDR test requires root access")

        memtester_path = params.get("memtester_path")
        remote_path = params.get("remote_path", "/data/local/tmp/memtester")

        if memtester_path:
            self._push_file(serial, memtester_path, remote_path)
            self._run_shell(serial, ["chmod", "755", remote_path])
            self._log(f"memtester 已推送到 {remote_path}")

    def execute(self, serial: str, params: Dict[str, Any]) -> TestResult:
        remote_path = params.get("remote_path", "/data/local/tmp/memtester")
        mem_size_mb = int(params.get("mem_size_mb", 512))
        loops = int(params.get("loops", 1))

        self._log(f"执行 memtester: size={mem_size_mb}MB, loops={loops}")
        result = self._run_shell(
            serial,
            [remote_path, str(mem_size_mb), str(loops)],
            timeout=mem_size_mb * loops * 10,
        )
        status = "FINISHED" if result.returncode == 0 else "FAILED"

        return TestResult(
            status=status,
            exit_code=result.returncode,
            error_code=None if status == "FINISHED" else "MEMTESTER_FAILED",
            log_summary=(result.stdout or "")[-2000:],
        )
