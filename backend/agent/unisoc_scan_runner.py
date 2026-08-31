"""UnisocScanRunner — UNISOC archive chain (ADR-0032 D4c)."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Sequence

try:
    from backend.agent.aee.paths import get_aee_local_root
    from backend.agent.scan_runner import ScanRunner, _SCAN_SUBPROCESS_TIMEOUT
except ImportError:
    from agent.aee.paths import get_aee_local_root
    from agent.scan_runner import ScanRunner, _SCAN_SUBPROCESS_TIMEOUT

logger = logging.getLogger(__name__)


class UnisocScanRunner:
    """Agent-side scan_log_gt → scan_result.py → upload(unisoc/)."""

    _instance: Optional["UnisocScanRunner"] = None

    def __init__(self) -> None:
        self._scan_python = ""
        self._scan_script = ""
        self._result_python = ""
        self._result_script = ""
        self._side = "shanghai"
        self._configured = False

    @classmethod
    def instance(cls) -> "UnisocScanRunner":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def _reset_for_tests(cls) -> None:
        cls._instance = None

    def configure(
        self,
        *,
        scan_tool_python: str = "",
        scan_tool_script: str = "",
        result_python: str = "",
        result_script: str = "",
        hdd_root: str = "",
        side: Optional[str] = None,
        force: bool = False,
    ) -> None:
        if self._configured and not force:
            return
        self._scan_python = scan_tool_python or os.getenv("STP_UNISOC_LOG_SCAN_PYTHON", "").strip()
        self._scan_script = scan_tool_script or os.getenv("STP_UNISOC_LOG_SCAN_SCRIPT", "").strip()
        self._result_python = result_python or os.getenv("STP_UNISOC_SCAN_RESULT_PYTHON", "").strip()
        self._result_script = result_script or os.getenv("STP_UNISOC_SCAN_RESULT_SCRIPT", "").strip()
        if not side:
            tag = os.getenv("STP_DEDUP_SCAN_TAG", "").strip().lower()
            side = "factory" if "factory" in tag else "shanghai"
        self._side = side
        self._configured = bool(
            self._scan_python and self._scan_script
            and self._result_python and self._result_script
        )

    def is_configured(self) -> bool:
        return self._configured

    def run_scan_and_upload(
        self,
        plan_run_id: int,
        host_id: str,
        *,
        is_final: bool,
        device_serials: Sequence[str] = (),
        run_date_stamps: Sequence[str] = (),
    ) -> None:
        if not self.is_configured():
            return
        org_xls = self.run_local_scan(
            plan_run_id=plan_run_id,
            host_id=host_id,
            is_final=is_final,
            device_serials=device_serials,
            run_date_stamps=run_date_stamps,
        )
        if not org_xls:
            return
        result_xls = self.run_scan_result(org_xls, plan_run_id, host_id)
        try:
            from backend.agent.upload_manager import UploadManager
        except ImportError:
            from agent.upload_manager import UploadManager
        uploader = UploadManager.instance()
        if not uploader.is_configured():
            return
        uploader.upload_scan_report(plan_run_id, host_id, org_xls, platform_subdir="unisoc")
        if result_xls:
            uploader.upload_scan_report(plan_run_id, host_id, result_xls, platform_subdir="unisoc")

    def _build_argv(self, *, is_final: bool, scan_root: str) -> List[str]:
        argv = [
            self._scan_python, self._scan_script,
            "-m", "sprd", "-d", scan_root, "-side", self._side,
        ]
        if is_final:
            argv.append("-end")
        return argv

    def run_local_scan(
        self,
        plan_run_id: int,
        host_id: str,
        *,
        is_final: bool = False,
        device_serials: Sequence[str] = (),
        run_date_stamps: Sequence[str] = (),
    ) -> Optional[str]:
        scan_root = ScanRunner.instance()._prepare_scan_root(
            plan_run_id, device_serials, run_date_stamps,
        )
        if scan_root is None:
            return None
        scan_start = time.time()
        argv = self._build_argv(is_final=is_final, scan_root=scan_root)
        cwd = str(Path(self._scan_script).parent)
        try:
            result = subprocess.run(
                argv, cwd=cwd, capture_output=True, text=True, timeout=_SCAN_SUBPROCESS_TIMEOUT,
            )
        except Exception:
            logger.exception("unisoc_scan_exception plan_run=%d", plan_run_id)
            return None
        if result.returncode != 0:
            return None
        fresh = [
            c for c in Path(scan_root).glob("**/Result_*_org.xls")
            if c.stat().st_mtime >= scan_start - 1
        ]
        if not fresh:
            return None
        return str(max(fresh, key=lambda p: p.stat().st_mtime).resolve())

    def run_scan_result(self, org_xls_path: str, plan_run_id: int, host_id: str) -> Optional[str]:
        argv = [self._result_python, self._result_script, org_xls_path]
        cwd = str(Path(self._result_script).parent)
        try:
            result = subprocess.run(
                argv, cwd=cwd, capture_output=True, text=True, timeout=_SCAN_SUBPROCESS_TIMEOUT,
            )
        except Exception:
            return None
        if result.returncode != 0:
            return None
        output_path = (result.stdout or "").strip()
        if output_path and Path(output_path).exists():
            return str(Path(output_path).resolve())
        return None


__all__ = ["UnisocScanRunner"]
